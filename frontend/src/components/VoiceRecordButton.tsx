/**
 * Voice-to-text button for the AI-intake chat composer.
 *
 * Idle: a mic button. Recording: the component grows into a full-width
 * "recording strip" — live waveform of the actual mic signal (WebAudio
 * AnalyserNode on canvas), a monospace timer, cancel (discard, Esc) and
 * accept (stop → transcribe). The parent hides the textarea/send while
 * recording via `onRecordingChange` so the strip takes the composer row.
 *
 * Records with MediaRecorder, uploads to /api/v1/intake-chat/transcriptions,
 * hands the transcribed text back via `onTranscript`. Manual fetch (not
 * useMutation) is deliberate: the app-wide MutationCache (main.tsx) toasts
 * every mutation error itself, so a useMutation here would double-toast.
 *
 * Renders nothing if the browser lacks getUserMedia/MediaRecorder support.
 */
import { useEffect, useRef, useState } from 'react'
import { Check, Loader2, Mic, X } from 'lucide-react'
import { toast } from 'sonner'
import { cn } from '@/lib/utils'
import { ApiError } from '@/api/client'
import { getErrorMessage } from '@/lib/errors'
import { intakeChatApi } from '@/api/intakeChat'

const MAX_RECORDING_MS = 180_000
// Turn the timer red this close to the auto-stop cap.
const CAP_WARN_S = 30
// ponytail: rough silence/noise-floor guard, not a real audio-length check —
// good enough to skip empty near-instant taps without decoding the blob.
const MIN_BLOB_BYTES = 1000

// VAD (voice-activity detection), used only when autoStopOnSilence is on.
// Rides the same ~50ms sampling grid the waveform already runs on.
const VAD_CALIBRATION_MS = 300 // first stretch of a recording: sample the noise floor, don't judge speech yet
const VAD_FLOOR_MULTIPLIER = 2.5 // silence threshold = max(floor * this, VAD_MIN_THRESHOLD)
const VAD_MIN_THRESHOLD = 0.012 // floor for a near-silent room/mic
const VAD_SAMPLE_MS = 50 // approximate step between samples (matches the sampling grid below)
const VAD_MIN_SPEECH_MS = 1000 // must hear this much cumulative speech before auto-stop is allowed to arm
const VAD_SILENCE_STOP_MS = 1800 // once armed, this much continuous silence triggers stopRecording()

function pickMime(): { mime: string; ext: string } | null {
  if (typeof MediaRecorder.isTypeSupported === 'function') {
    if (MediaRecorder.isTypeSupported('audio/webm;codecs=opus')) {
      return { mime: 'audio/webm;codecs=opus', ext: 'webm' }
    }
    if (MediaRecorder.isTypeSupported('audio/mp4')) {
      return { mime: 'audio/mp4', ext: 'mp4' }
    }
  }
  return null
}

function recordErrorMessage(err: unknown): string {
  const name = err instanceof DOMException ? err.name : undefined
  if (name === 'NotAllowedError' || name === 'SecurityError') {
    return 'Доступ к микрофону запрещён — разрешите доступ в настройках браузера.'
  }
  if (name === 'NotFoundError') {
    return 'Микрофон не найден.'
  }
  const code = err instanceof ApiError ? (err.body as { error?: { code?: string } } | null)?.error?.code : null
  if (code === 'TRANSCRIPTION_UNAVAILABLE') {
    return 'Распознавание речи не настроено на сервере.'
  }
  return getErrorMessage(err, 'Не удалось распознать запись.')
}

function formatElapsed(s: number): string {
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

type RecordState = 'idle' | 'recording' | 'transcribing'

export function VoiceRecordButton({
  disabled,
  onTranscript,
  onTranscribingChange,
  onRecordingChange,
  autoStopOnSilence,
  startSignal,
}: {
  disabled: boolean
  onTranscript: (text: string) => void
  onTranscribingChange?: (transcribing: boolean) => void
  onRecordingChange?: (recording: boolean) => void
  // Voice-mode loop: auto-stop on silence (VAD) instead of waiting for a manual tap.
  autoStopOnSilence?: boolean
  // Voice-mode loop: bump to a new number to imperatively (re-)arm the mic,
  // e.g. right after the assistant finishes speaking. No-ops unless idle.
  startSignal?: number
}) {
  const [state, setState] = useState<RecordState>('idle')
  const [elapsed, setElapsed] = useState(0)
  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const chunksRef = useRef<Blob[]>([])
  const autoStopRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const extRef = useRef('webm')
  // Waveform: analyser feeds a rolling level history painted on the canvas
  // by a rAF loop. All refs — none of it should re-render React.
  const audioCtxRef = useRef<AudioContext | null>(null)
  const analyserRef = useRef<AnalyserNode | null>(null)
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const rafRef = useRef(0)
  const levelsRef = useRef<number[]>([])
  const lastSampleRef = useRef(0)
  const startedAtRef = useRef(0)
  // VAD state — reset per recording in startRecording(). See constants above.
  const vadFloorSumRef = useRef(0)
  const vadFloorCountRef = useRef(0)
  const vadThresholdRef = useRef<number | null>(null)
  const vadSpeechMsRef = useRef(0)
  const vadSilenceMsRef = useRef(0)
  const vadArmedRef = useRef(false)
  // Set by the cancel button: the onstop handler discards instead of uploading.
  const discardRef = useRef(false)
  // Re-entrancy guard for startRecording: set synchronously before the
  // getUserMedia await so a double-click can't race two concurrent starts.
  const startingRef = useRef(false)
  // Flips true on unmount, before tracks are stopped, so any recorder event
  // or in-flight promise that resolves afterward discards instead of acting
  // on a dead component.
  const cancelledRef = useRef(false)
  // Latest-ref pattern: recorder events fire long after the render that
  // started them (Modal cold start can take seconds), so callbacks must read
  // the current props, not the ones captured at recording start.
  const onTranscriptRef = useRef(onTranscript)
  onTranscriptRef.current = onTranscript
  const onTranscribingChangeRef = useRef(onTranscribingChange)
  onTranscribingChangeRef.current = onTranscribingChange
  const onRecordingChangeRef = useRef(onRecordingChange)
  onRecordingChangeRef.current = onRecordingChange
  const autoStopOnSilenceRef = useRef(autoStopOnSilence)
  autoStopOnSilenceRef.current = autoStopOnSilence

  const supported =
    typeof navigator !== 'undefined' &&
    !!navigator.mediaDevices?.getUserMedia &&
    typeof MediaRecorder !== 'undefined'

  const stopVisuals = () => {
    cancelAnimationFrame(rafRef.current)
    void audioCtxRef.current?.close().catch(() => {})
    audioCtxRef.current = null
    analyserRef.current = null
  }

  const cleanupStream = () => {
    stopVisuals()
    streamRef.current?.getTracks().forEach((t) => t.stop())
    streamRef.current = null
    if (autoStopRef.current) {
      clearTimeout(autoStopRef.current)
      autoStopRef.current = null
    }
  }

  useEffect(
    () => () => {
      // Order matters: flip the guard first so the onstop this triggers
      // discards the recording instead of uploading it.
      cancelledRef.current = true
      cleanupStream()
    },
    [],
  )

  useEffect(() => {
    onTranscribingChangeRef.current?.(state === 'transcribing')
    onRecordingChangeRef.current?.(state === 'recording')
    // Unmounting mid-flight must not leave the parent's "Расшифровка…" bubble
    // or hidden composer stuck forever.
    return () => {
      onTranscribingChangeRef.current?.(false)
      onRecordingChangeRef.current?.(false)
    }
  }, [state])

  const paintWaveform = () => {
    const canvas = canvasRef.current
    const ctx = canvas?.getContext('2d')
    if (!canvas || !ctx) return
    const dpr = window.devicePixelRatio || 1
    const w = canvas.clientWidth
    const h = canvas.clientHeight
    if (canvas.width !== Math.round(w * dpr)) canvas.width = Math.round(w * dpr)
    if (canvas.height !== Math.round(h * dpr)) canvas.height = Math.round(h * dpr)
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    ctx.clearRect(0, 0, w, h)
    ctx.fillStyle = getComputedStyle(canvas).color
    const levels = levelsRef.current
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      // Calm level meter instead of a scrolling waveform.
      const lv = levels[levels.length - 1] ?? 0
      ctx.globalAlpha = 0.7
      ctx.fillRect(0, h / 2 - 2, Math.max(4, lv * w), 4)
      return
    }
    const barW = 3
    const step = barW + 2
    const visible = levels.slice(-Math.floor(w / step))
    visible.forEach((lv, i) => {
      const x = w - (visible.length - i) * step
      const barH = Math.max(2, lv * (h - 2))
      // Older peaks fade — the fresh signal reads at the right edge.
      ctx.globalAlpha = 0.3 + 0.7 * ((i + 1) / visible.length)
      ctx.beginPath()
      if (typeof ctx.roundRect === 'function') ctx.roundRect(x, (h - barH) / 2, barW, barH, 1.5)
      else ctx.rect(x, (h - barH) / 2, barW, barH)
      ctx.fill()
    })
    ctx.globalAlpha = 1
  }

  const frame = (now: number) => {
    const sec = Math.floor((now - startedAtRef.current) / 1000)
    setElapsed((e) => (e === sec ? e : sec))
    const analyser = analyserRef.current
    // Sample on a fixed ~50ms grid so the roll speed doesn't depend on fps.
    if (analyser && now - lastSampleRef.current >= 50) {
      lastSampleRef.current = now
      const buf = new Uint8Array(analyser.fftSize)
      analyser.getByteTimeDomainData(buf)
      let sum = 0
      for (const v of buf) sum += (v - 128) * (v - 128)
      const rms = Math.sqrt(sum / buf.length) / 128
      levelsRef.current.push(Math.min(1, rms * 4))
      if (levelsRef.current.length > 400) levelsRef.current.shift()
      if (autoStopOnSilenceRef.current) {
        const elapsedMs = now - startedAtRef.current
        if (elapsedMs < VAD_CALIBRATION_MS) {
          vadFloorSumRef.current += rms
          vadFloorCountRef.current += 1
        } else {
          if (vadThresholdRef.current === null) {
            const floor = vadFloorCountRef.current > 0 ? vadFloorSumRef.current / vadFloorCountRef.current : 0
            vadThresholdRef.current = Math.max(floor * VAD_FLOOR_MULTIPLIER, VAD_MIN_THRESHOLD)
          }
          if (rms > vadThresholdRef.current) {
            vadSpeechMsRef.current += VAD_SAMPLE_MS
            vadSilenceMsRef.current = 0
            if (vadSpeechMsRef.current >= VAD_MIN_SPEECH_MS) vadArmedRef.current = true
          } else {
            vadSilenceMsRef.current += VAD_SAMPLE_MS
            if (vadArmedRef.current && vadSilenceMsRef.current >= VAD_SILENCE_STOP_MS) stopRecording()
          }
        }
      }
    }
    paintWaveform()
    rafRef.current = requestAnimationFrame(frame)
  }

  const startRecording = async () => {
    if (startingRef.current) return
    startingRef.current = true
    const picked = pickMime()
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      if (cancelledRef.current) {
        stream.getTracks().forEach((t) => t.stop())
        return
      }
      streamRef.current = stream
      const recorder = picked ? new MediaRecorder(stream, { mimeType: picked.mime }) : new MediaRecorder(stream)
      extRef.current = picked?.ext ?? 'webm'
      chunksRef.current = []
      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data)
      }
      recorder.onstop = () => void handleStopped()
      mediaRecorderRef.current = recorder
      // Waveform is best-effort — recording must work even if WebAudio fails.
      try {
        const audioCtx = new AudioContext()
        const analyser = audioCtx.createAnalyser()
        analyser.fftSize = 512
        audioCtx.createMediaStreamSource(stream).connect(analyser)
        audioCtxRef.current = audioCtx
        analyserRef.current = analyser
      } catch {
        // no waveform, just the timer
      }
      discardRef.current = false
      levelsRef.current = []
      startedAtRef.current = performance.now()
      lastSampleRef.current = 0
      vadFloorSumRef.current = 0
      vadFloorCountRef.current = 0
      vadThresholdRef.current = null
      vadSpeechMsRef.current = 0
      vadSilenceMsRef.current = 0
      vadArmedRef.current = false
      setElapsed(0)
      recorder.start()
      setState('recording')
      rafRef.current = requestAnimationFrame(frame)
      autoStopRef.current = setTimeout(() => stopRecording(), MAX_RECORDING_MS)
    } catch (err) {
      if (!cancelledRef.current) {
        cleanupStream()
        toast.error(recordErrorMessage(err))
      }
    } finally {
      startingRef.current = false
    }
  }

  const stopRecording = () => {
    // stop() on an inactive recorder throws InvalidStateError — possible if a
    // stream-initiated stop (mic unplugged) races a click on the stop button.
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
      mediaRecorderRef.current.stop()
    }
    if (autoStopRef.current) {
      clearTimeout(autoStopRef.current)
      autoStopRef.current = null
    }
  }

  const cancelRecording = () => {
    discardRef.current = true
    stopRecording()
  }

  // Esc discards the recording — hands stay on the keyboard while dictating.
  useEffect(() => {
    if (state !== 'recording') return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') cancelRecording()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [state])

  // Imperative "start" signal for the voice-mode loop: the parent bumps
  // startSignal to a new number (e.g. once TTS playback ends) to re-arm the
  // mic without the user tapping it. Compares against the previous value so
  // it never fires on mount, and only starts from idle when not disabled.
  const prevStartSignalRef = useRef(startSignal)
  useEffect(() => {
    if (startSignal === undefined || startSignal === prevStartSignalRef.current) return
    prevStartSignalRef.current = startSignal
    if (state === 'idle' && !disabled) void startRecording()
  }, [startSignal, state, disabled])

  const handleStopped = async () => {
    // Covers stream-initiated stops too (permission revoked, mic unplugged)
    // that never go through stopRecording().
    if (autoStopRef.current) {
      clearTimeout(autoStopRef.current)
      autoStopRef.current = null
    }
    stopVisuals()
    streamRef.current?.getTracks().forEach((t) => t.stop())
    streamRef.current = null
    const chunks = chunksRef.current
    chunksRef.current = []
    if (cancelledRef.current) return // discard: no upload, no onTranscript, no setState
    if (discardRef.current) {
      setState('idle')
      return
    }
    const blob = new Blob(chunks, { type: mediaRecorderRef.current?.mimeType })
    if (blob.size < MIN_BLOB_BYTES) {
      toast.error('Запись слишком короткая')
      setState('idle')
      return
    }
    setState('transcribing')
    try {
      const { data } = await intakeChatApi.transcribe(blob, `voice.${extRef.current}`)
      if (cancelledRef.current) return
      const text = data.text.trim()
      if (!text) {
        toast.error('Не удалось распознать речь')
      } else {
        onTranscriptRef.current(text)
      }
    } catch (err) {
      if (!cancelledRef.current) toast.error(recordErrorMessage(err))
    } finally {
      if (!cancelledRef.current) setState('idle')
    }
  }

  if (!supported) return null

  if (state === 'recording') {
    const nearCap = elapsed >= MAX_RECORDING_MS / 1000 - CAP_WARN_S
    return (
      <div className="flex h-9 min-w-0 flex-1 items-center gap-2 pl-2">
        <span
          aria-hidden="true"
          className="h-2 w-2 flex-none animate-pulse rounded-full bg-destructive motion-reduce:animate-none"
        />
        <span
          className={cn(
            'w-9 flex-none font-mono text-[12px] font-medium tabular-nums',
            nearCap ? 'text-destructive' : 'text-muted-foreground',
          )}
          title={nearCap ? 'Запись остановится автоматически на 3:00' : undefined}
        >
          {formatElapsed(elapsed)}
        </span>
        <canvas ref={canvasRef} aria-hidden="true" className="h-7 min-w-0 flex-1 text-foreground" />
        <button
          type="button"
          onClick={cancelRecording}
          aria-label="Отменить запись"
          title="Отменить запись (Esc)"
          className="flex h-9 w-9 flex-none items-center justify-center rounded-full text-muted-foreground hover:bg-muted hover:text-destructive focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <X className="h-5 w-5" />
        </button>
        <button
          type="button"
          autoFocus
          onClick={stopRecording}
          aria-label="Остановить запись и распознать"
          title="Готово — распознать"
          className="flex h-9 w-9 flex-none items-center justify-center rounded-full bg-primary text-primary-foreground hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <Check className="h-5 w-5" />
        </button>
      </div>
    )
  }

  return (
    <button
      type="button"
      onClick={() => void startRecording()}
      disabled={disabled || state === 'transcribing'}
      aria-label={state === 'transcribing' ? 'Распознавание речи…' : 'Записать голосовое сообщение'}
      className="flex h-9 w-9 flex-none items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-40"
    >
      {state === 'transcribing' ? <Loader2 className="h-5 w-5 animate-spin" /> : <Mic className="h-5 w-5" />}
    </button>
  )
}
