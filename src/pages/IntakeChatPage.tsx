import { useCallback, useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import {
  AlertCircle,
  ArrowUp,
  Bot,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Circle,
  FileText,
  Loader2,
  Pencil,
  Send,
  Sparkles,
  Trash2,
  Volume2,
  VolumeX,
} from 'lucide-react'
import { toast } from 'sonner'
import { cn } from '@/lib/utils'
import { ApiError } from '@/api/client'
import { getErrorMessage } from '@/lib/errors'
import {
  intakeChatApi,
  type ChatMessage,
  type FieldState,
  type SessionDetail,
} from '@/api/intakeChat'
import { DocumentAttach } from '@/components/DocumentAttach'
import { LoadingSpinner } from '@/components/LoadingSpinner'
import { VoiceRecordButton } from '@/components/VoiceRecordButton'
import { PromptInput, PromptInputActions, PromptInputTextarea } from '@/components/ui/prompt-input'

export const INTAKE_SESSION_STORAGE_KEY = 'intake:sessionId'
const VOICE_MODE_STORAGE_KEY = 'intake:voiceMode'


const sharedAudio: HTMLAudioElement | null = typeof Audio !== 'undefined' ? new Audio() : null

function makeSilentWavUrl(): string {
  // 44-byte WAV header + ~50ms of 16-bit PCM silence, built at runtime so we
  // don't carry a giant base64 literal. A valid non-empty clip matters: the
  // old zero-length-data clip failed to unlock playback on some browsers.
  if (typeof URL === 'undefined' || typeof Blob === 'undefined') return ''
  const sampleRate = 8000
  const n = Math.floor(sampleRate * 0.05)
  const buf = new ArrayBuffer(44 + n * 2)
  const dv = new DataView(buf)
  const w = (o: number, str: string) => {
    for (let i = 0; i < str.length; i++) dv.setUint8(o + i, str.charCodeAt(i))
  }
  w(0, 'RIFF'); dv.setUint32(4, 36 + n * 2, true); w(8, 'WAVE')
  w(12, 'fmt '); dv.setUint32(16, 16, true); dv.setUint16(20, 1, true); dv.setUint16(22, 1, true)
  dv.setUint32(24, sampleRate, true); dv.setUint32(28, sampleRate * 2, true)
  dv.setUint16(32, 2, true); dv.setUint16(34, 16, true)
  w(36, 'data'); dv.setUint32(40, n * 2, true)
  return URL.createObjectURL(new Blob([buf], { type: 'audio/wav' }))
}
const SILENT_WAV = makeSilentWavUrl()

// Flips true only once the silent clip actually plays — a still-blocked
// attempt leaves it false so the next gesture retries. Module-level so it
// survives the pre-session → active remount.
let audioUnlocked = false
function unlockAudio() {
  if (!sharedAudio || audioUnlocked) return
  sharedAudio.src = SILENT_WAV
  const p = sharedAudio.play()
  if (!p) {
    audioUnlocked = true
    return
  }
  p.then(() => {
    audioUnlocked = true
    sharedAudio?.pause()
    if (sharedAudio) sharedAudio.currentTime = 0
  }).catch(() => {
    // still blocked (e.g. no real gesture yet) — a later gesture retries
  })
}

// Short "listening" cue (WebAudio oscillator, no audio assets) played right
// before the mic auto-arms, so the user knows to start talking.
function playListenBeep() {
  try {
    const Ctx = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
    if (!Ctx) return
    const ctx = new Ctx()
    const osc = ctx.createOscillator()
    const gain = ctx.createGain()
    osc.frequency.value = 880
    gain.gain.value = 0.05
    osc.connect(gain)
    gain.connect(ctx.destination)
    osc.start()
    osc.stop(ctx.currentTime + 0.12)
    osc.onended = () => void ctx.close()
  } catch {
    // best-effort cue only — never block the voice loop over it
  }
}

const GREETING =
  'Здравствуйте! Я помогу оформить бизнес-заявку. Опишите задачу своими словами — ' +
  'я задам уточняющие вопросы и соберу черновик. Можно приложить документы: изучу их и ' +
  'заполню, что смогу, заранее.'

// Starter prompts for the empty state — clicking one seeds the composer so the
// user isn't staring at a blank field (they edit and send).
const STARTERS: { label: string; seed: string }[] = [
  { label: 'Автоматизировать рутину', seed: 'Хочу автоматизировать рутинную операцию: ' },
  { label: 'Отчёт или выгрузка данных', seed: 'Нужен отчёт или выгрузка данных: ' },
  { label: 'Доработать систему', seed: 'Нужно доработать существующую систему: ' },
  { label: 'Не знаю, с чего начать', seed: 'Не знаю, с чего начать — помогите оформить заявку по шагам.' },
]

// Round primary action at the right end of the composer row; grey while it
// can't fire (empty draft / assistant busy) so the yellow reads as "ready".
const SEND_BUTTON_CLASS =
  'flex h-9 w-9 flex-none items-center justify-center rounded-full bg-primary text-primary-foreground transition-colors hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:bg-muted disabled:text-muted-foreground'

function errorMessage(err: unknown): string {
  const body = err instanceof ApiError ? (err.body as { error?: { code?: string } } | null) : null
  if (body?.error?.code === 'LLM_UNAVAILABLE') {
    return 'ИИ-ассистент не настроен на сервере (OPENAI_API_KEY). Обратитесь к администратору.'
  }
  return getErrorMessage(err, 'Что-то пошло не так. Попробуйте ещё раз.')
}

function Bubble({ role, content }: { role: 'user' | 'assistant'; content: string }) {
  const isUser = role === 'user'
  return (
    <div className={cn('flex gap-2.5', isUser && 'justify-end')}>
      {!isUser && (
        <div className="flex h-8 w-8 flex-none items-center justify-center rounded-[9px] bg-primary text-primary-foreground">
          <Bot className="h-[18px] w-[18px]" />
        </div>
      )}
      <div
        className={cn(
          'max-w-[85%] whitespace-pre-wrap sm:max-w-[75%] rounded-2xl px-4 py-2.5 text-[13.5px] leading-relaxed',
          isUser
            ? 'rounded-br-md bg-foreground text-background'
            : 'rounded-bl-md border border-border bg-card',
        )}
      >
        {content}
      </div>
    </div>
  )
}

function TypingBubble({ label }: { label: string }) {
  return (
    <div className="flex gap-2.5">
      <div className="flex h-8 w-8 flex-none items-center justify-center rounded-[9px] bg-primary text-primary-foreground">
        <Bot className="h-[18px] w-[18px]" />
      </div>
      <div className="flex items-center gap-2 rounded-2xl rounded-bl-md border border-border bg-card px-4 py-2.5 text-[13px] text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
        {label}
      </div>
    </div>
  )
}

// Mirrors TypingBubble but right-aligned like a user message, shown while a
// recorded voice message is being transcribed on the server.
function TranscribingBubble() {
  return (
    <div className="flex justify-end gap-2.5">
      <div className="flex items-center gap-2 rounded-2xl rounded-br-md bg-foreground/80 px-4 py-2.5 text-[13px] text-background">
        <Loader2 className="h-4 w-4 animate-spin" />
        Расшифровка голосового сообщения…
      </div>
    </div>
  )
}

// Voice-mode pill for the composer's action row: icon-only when off, expands
// to show its label when on — the mode is otherwise invisible until the mic
// arms itself after a reply.
function VoiceModeToggle({ on, onToggle }: { on: boolean; onToggle: () => void }) {
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-pressed={on}
      aria-label={on ? 'Выключить голосовой режим' : 'Включить голосовой режим'}
      title="Голосовой режим: ассистент отвечает голосом, микрофон включается сам. Лучше в наушниках."
      className={cn(
        'flex h-9 flex-none items-center rounded-full border px-2.5 text-[12px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
        on
          ? 'border-primary bg-primary/15 text-foreground'
          : 'border-transparent text-muted-foreground hover:bg-muted hover:text-foreground',
      )}
    >
      {on ? <Volume2 className="h-4 w-4 flex-none" /> : <VolumeX className="h-4 w-4 flex-none" />}
      <span
        className={cn(
          'overflow-hidden whitespace-nowrap transition-all duration-200 motion-reduce:transition-none',
          on ? 'ml-1.5 max-w-[9rem] opacity-100' : 'max-w-0 opacity-0',
        )}
      >
        Голосовой режим
      </span>
    </button>
  )
}

export default function IntakeChatPage() {
  const navigate = useNavigate()
  const qc = useQueryClient()

  // Resume a session if one is stored; otherwise start in the "pre-session"
  // composer state (no session created yet) and materialize it lazily on the
  // first action. This removes the old /intake pre-step: the user lands
  // straight in the conversation workspace.
  const [sessionId, setSessionId] = useState<string | null>(
    () => sessionStorage.getItem(INTAKE_SESSION_STORAGE_KEY),
  )

  const [detail, setDetail] = useState<SessionDetail | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [fields, setFields] = useState<FieldState[]>([])
  const [isReady, setIsReady] = useState(false)
  const [hasOpener, setHasOpener] = useState(false)
  const [input, setInput] = useState('')
  const [thinking, setThinking] = useState(false)
  const [transcribing, setTranscribing] = useState(false)
  // While recording, the VoiceRecordButton grows into a full-width strip and
  // the textarea/send are hidden — this mirrors its internal state.
  const [recording, setRecording] = useState(false)
  const [finalizing, setFinalizing] = useState(false)
  const [fatal, setFatal] = useState<string | null>(null)
  // Pre-session: documents chosen before the session exists (passed to
  // startSession — the backend only attaches documents at session start).
  const [pendingDocIds, setPendingDocIds] = useState<string[]>([])
  const [starting, setStarting] = useState(false)
  // Mobile only: whether the readiness bottom-sheet is expanded to show fields.
  const [sheetOpen, setSheetOpen] = useState(false)
  // Inline draft-title editing in the active-session header.
  const [editingTitle, setEditingTitle] = useState(false)
  const [titleDraft, setTitleDraft] = useState('')
  // Voice mode: walkie-talkie loop (listen -> transcribe -> send -> speak ->
  // listen again). Persisted so the toggle survives a reload.
  const [voiceMode, setVoiceMode] = useState(() => localStorage.getItem(VOICE_MODE_STORAGE_KEY) === '1')
  const [speaking, setSpeaking] = useState(false)
  // Bumped to re-arm the mic (VoiceRecordButton's startSignal prop) once TTS
  // playback for a reply finishes.
  const [listenSignal, setListenSignal] = useState(0)
  const scrollRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  // Set while an interrupt (voice mode off / Esc / mic click) is stopping
  // playback, so the in-flight speakReply() doesn't re-arm the loop anyway.
  const suppressArmRef = useRef(false)
  // Resolves the "wait for playback to end" promise inside speakReply — also
  // used by stopSpeaking() to unblock it early on an interrupt.
  const speakResolveRef = useRef<(() => void) | null>(null)
  // Latest-value refs: the re-arm logic runs inside async callbacks captured
  // at an earlier render — notably the pre-session turn that just created the
  // session, where `detail` was still null and `voiceMode` could since have
  // been turned off. Reading refs avoids acting on that stale closure state.
  const voiceModeRef = useRef(voiceMode)
  voiceModeRef.current = voiceMode
  const sessionActiveRef = useRef(false)
  sessionActiveRef.current = detail?.session.status === 'active'

  useEffect(() => {
    localStorage.setItem(VOICE_MODE_STORAGE_KEY, voiceMode ? '1' : '0')
  }, [voiceMode])

  const seedComposer = (text: string) => {
    setInput(text)
    requestAnimationFrame(() => {
      const el = inputRef.current
      if (el) {
        el.focus()
        el.setSelectionRange(el.value.length, el.value.length)
      }
    })
  }

  const preSession = !sessionId

  // Resumable drafts (active, not-yet-submitted sessions) shown on the empty
  // workspace so a started request is never lost.
  const draftsQuery = useQuery({
    queryKey: ['intake-sessions'],
    queryFn: () => intakeChatApi.list(50),
    enabled: preSession,
  })
  const activeDrafts = (draftsQuery.data?.data ?? []).filter((s) => s.status === 'active')

  const seedFrom = useCallback((data: SessionDetail) => {
    setDetail(data)
    setMessages(data.messages)
    setHasOpener(data.messages.length > 0)
    setFields(data.fields)
    setIsReady(data.is_ready)
  }, [])

  // Resume: load (and, while background analysis runs, poll) the session.
  const sessionQuery = useQuery({
    queryKey: ['intake-chat-session', sessionId],
    queryFn: () => intakeChatApi.getSession(sessionId as string),
    enabled: !!sessionId,
    refetchOnWindowFocus: false,
    refetchInterval: (query) => {
      // Poll only while an ACTIVE session is being analyzed — a non-active
      // session's 'pending' can never resolve (the background pass no-ops).
      const s = query.state.data?.data.session
      return s?.analysis_status === 'pending' && s.status === 'active' ? 2500 : false
    },
  })

  useEffect(() => {
    if (sessionQuery.isError) setFatal(errorMessage(sessionQuery.error))
  }, [sessionQuery.isError, sessionQuery.error])

  useEffect(() => {
    const data = sessionQuery.data?.data
    if (!data) return
    // A discarded session can linger in sessionStorage (discarded in another
    // tab, or orphaned long ago) — resuming it dead-ends the workspace, so
    // drop it and start fresh in the pre-session composer.
    if (data.session.status === 'discarded') {
      sessionStorage.removeItem(INTAKE_SESSION_STORAGE_KEY)
      setSessionId(null)
      setDetail(null)
      return
    }
    seedFrom(data)
  }, [sessionQuery.data, seedFrom])

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages, thinking, starting, transcribing])

  // Prewarm the transcription GPU as soon as the chat opens so the first
  // voice message doesn't pay the full cold-start cost. Fire-and-forget.
  useEffect(() => {
    void intakeChatApi.warmupTranscription().catch(() => {})
  }, [])

  // Unlock TTS playback on the FIRST user gesture of any kind — not just the
  // voice-mode toggle. voiceMode persists in localStorage, so a returning user
  // can start the loop (tap the mic) without clicking the toggle this session;
  // without a gesture-blessed <audio> element the browser silently blocks
  // audio.play() and every assistant reply is inaudible. Idempotent (guarded
  // by audioUnlocked), so leaving the listeners on until unmount is harmless.
  useEffect(() => {
    if (audioUnlocked) return
    const onGesture = () => unlockAudio()
    window.addEventListener('pointerdown', onGesture)
    window.addEventListener('keydown', onGesture)
    return () => {
      window.removeEventListener('pointerdown', onGesture)
      window.removeEventListener('keydown', onGesture)
    }
  }, [])

  const analysisStatus = detail?.session.analysis_status
  const analyzing = analysisStatus === 'pending' && detail?.session.status === 'active'
  const analysisFailed = analysisStatus === 'failed'

  // Create the session lazily (with any attached documents), then optionally
  // send the first user message.
  const startAndMaybeSend = useCallback(
    async (firstMessage?: string): Promise<string | null> => {
      if (starting) return null
      setStarting(true)
      try {
        const { data } = await intakeChatApi.startSession(undefined, pendingDocIds)
        sessionStorage.setItem(INTAKE_SESSION_STORAGE_KEY, data.session.id)
        // Send the first message BEFORE enabling the resume query, so its very
        // first getSession sees the persisted turn and cannot clobber the local
        // messages. A message can't be sent while documents are still analysing
        // (backend rejects it), so only the no-documents path auto-sends.
        let reply: string | null = null
        if (firstMessage && data.session.analysis_status !== 'pending') {
          const turn = await intakeChatApi.sendMessage(data.session.id, firstMessage)
          const userMsg: ChatMessage = {
            id: `local-${Date.now()}`,
            seq: (data.messages.length || 0) + 1,
            role: 'user',
            content: firstMessage,
            created_at: new Date().toISOString(),
          }
          setDetail({ ...data, session: turn.data.session })
          setMessages([...data.messages, userMsg, turn.data.reply])
          setHasOpener(data.messages.length > 0)
          setFields(turn.data.fields)
          setIsReady(turn.data.is_ready)
          reply = turn.data.reply.content
        } else {
          seedFrom(data)
        }
        setSessionId(data.session.id) // enables the resume query + polling
        return reply
      } catch (err) {
        // Same append-preserving restore as sendText's catch — otherwise a
        // dictated (or typed) first message just vanishes on failure.
        if (firstMessage) {
          setInput((prev) => (prev.trim() ? `${prev} ${firstMessage}` : firstMessage))
        }
        toast.error(errorMessage(err))
        return null
      } finally {
        setStarting(false)
      }
    },
    [pendingDocIds, seedFrom, starting],
  )

  // Send a message to the active session. Extracted from send() so a
  // transcribed voice message can be sent the same way as typed text,
  // without going through the composer's input state. Returns the assistant's
  // reply text (or null on no-op/failure) so the voice loop can speak it.
  const sendText = useCallback(
    async (content: string): Promise<string | null> => {
      if (!content || !detail) return null
      if (thinking || analyzing || starting) return null
      setThinking(true)
      const optimistic: ChatMessage = {
        id: `local-${Date.now()}`,
        seq: messages.length + 1,
        role: 'user',
        content,
        created_at: new Date().toISOString(),
      }
      setMessages((m) => [...m, optimistic])
      try {
        const { data } = await intakeChatApi.sendMessage(detail.session.id, content)
        setDetail((d) => (d ? { ...d, session: data.session } : d))
        setMessages((m) => [...m, data.reply])
        setFields(data.fields)
        setIsReady(data.is_ready)
        return data.reply.content
      } catch (err) {
        setMessages((m) => m.filter((msg) => msg.id !== optimistic.id))
        // Append rather than replace: the typed path already cleared the
        // composer before calling sendText, so this restores it verbatim;
        // the voice path may have a half-typed draft sitting in `input`
        // that must survive a failed auto-send.
        setInput((prev) => (prev.trim() ? `${prev} ${content}` : content))
        toast.error(errorMessage(err))
        return null
      } finally {
        setThinking(false)
      }
    },
    [detail, thinking, analyzing, starting, messages.length],
  )

  // Re-arms the mic after a reply has been spoken (or failed to speak). Skips
  // the auto-arm if voice mode got turned off in the meantime, the tab is in
  // the background, or the session is no longer active (submitted/discarded).
  const armListenLoop = useCallback(() => {
    // Reads live refs (see above), not closure state — speakReply may have been
    // captured in the pre-session render, before the first message existed.
    if (!voiceModeRef.current || document.hidden) return
    if (!sessionActiveRef.current) return
    playListenBeep()
    setListenSignal((n) => n + 1)
  }, [])

  // Stops whatever the shared <audio> is doing right now (interrupt path:
  // mic click, voice mode off, Esc) and tells the in-flight speakReply (if
  // any) not to re-arm the loop once it unwinds.
  const stopSpeaking = useCallback(() => {
    suppressArmRef.current = true
    sharedAudio?.pause()
    speakResolveRef.current?.()
    setSpeaking(false)
  }, [])

  // Synthesizes and plays one assistant reply, then re-arms listening.
  // Best-effort: a synthesis or playback error toasts once and still re-arms
  // the loop — the reply text is already on screen either way.
  const speakReply = useCallback(
    async (text: string) => {
      const audio = sharedAudio
      if (!audio) {
        armListenLoop()
        return
      }
      setSpeaking(true)
      suppressArmRef.current = false
      let url: string | null = null
      try {
        const blob = await intakeChatApi.synthesizeSpeech(text)
        url = URL.createObjectURL(blob)
        audio.src = url
        await new Promise<void>((resolve) => {
          speakResolveRef.current = resolve
          audio.onended = () => resolve()
          audio.onerror = () => resolve()
          void audio.play().catch((err: unknown) => {
            // Most likely the autoplay policy blocked us (NotAllowedError) —
            // log it so a silent loop is never a silent mystery again.
            const e = err as { name?: string; message?: string }
            console.warn('[voice] TTS playback blocked/failed:', e?.name, e?.message)
            resolve()
          })
        })
      } catch {
        toast.error('Не удалось озвучить ответ — текст уже на экране.')
      } finally {
        speakResolveRef.current = null
        audio.onended = null
        audio.onerror = null
        if (url) URL.revokeObjectURL(url)
        setSpeaking(false)
        if (!suppressArmRef.current) armListenLoop()
      }
    },
    [armListenLoop],
  )

  const toggleVoiceMode = useCallback(() => {
    const next = !voiceMode
    // Unlock playback INSIDE the click handler — this is the user gesture
    // iOS/Safari requires; doing it in an effect risks missing the window.
    if (next) {
      unlockAudio()
      // Turning voice mode ON *is* the "start the conversation" gesture — arm
      // the mic now (beep + startSignal bump) so the walkie-talkie loop begins
      // without a separate mic tap. Works in the pre-session composer (detail
      // null) and an active session alike; skipped once submitted.
      if (!detail || detail.session.status === 'active') {
        playListenBeep()
        setListenSignal((n) => n + 1)
      }
    } else {
      stopSpeaking()
    }
    setVoiceMode(next)
  }, [voiceMode, detail, stopSpeaking])

  // Esc turns voice mode off (and, via the effect below, stops playback).
  // Recording itself is cancelled by VoiceRecordButton's own Esc handler —
  // we don't reach into the recorder here.
  useEffect(() => {
    if (!voiceMode) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setVoiceMode(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [voiceMode])

  // Mic click while the assistant is speaking interrupts playback and starts
  // listening — `recording` flips true via the button's own click handler,
  // this just silences the shared audio in response (the "existing button
  // click flow" the design calls for, no changes needed inside the button).
  useEffect(() => {
    if (recording && speaking) stopSpeaking()
  }, [recording, speaking, stopSpeaking])

  const send = useCallback(async () => {
    const content = input.trim()
    if (thinking || analyzing || starting) return
    if (preSession) {
      if (!content && pendingDocIds.length === 0) return
      // With documents attached, "Начать" kicks off analysis and any typed text
      // is kept in the composer for after (a turn can't be sent while analysing).
      const willSend = content.length > 0 && pendingDocIds.length === 0
      if (willSend) setInput('')
      await startAndMaybeSend(willSend ? content : undefined)
      return
    }
    if (!content) return
    setInput('')
    await sendText(content)
  }, [input, thinking, analyzing, starting, preSession, pendingDocIds.length, startAndMaybeSend, sendText])

  const finalize = useCallback(async () => {
    if (!detail || finalizing) return
    stopSpeaking() // voice loop stops the moment the session leaves 'active'
    setFinalizing(true)
    try {
      const { data } = await intakeChatApi.finalize(detail.session.id)
      setDetail(data)
      sessionStorage.removeItem(INTAKE_SESSION_STORAGE_KEY)
      void qc.invalidateQueries({ queryKey: ['tickets'] })
      toast.success(`Заявка создана и отправлена на триаж (${data.session.ticket_id})`)
    } catch (err) {
      toast.error(errorMessage(err))
    } finally {
      setFinalizing(false)
    }
  }, [detail, finalizing, qc, stopSpeaking])

  // Resume a stored draft (F).
  const resumeDraft = (id: string) => {
    sessionStorage.setItem(INTAKE_SESSION_STORAGE_KEY, id)
    setSessionId(id)
  }

  // Discard the active session and return to a fresh workspace (G).
  const discardSession = useCallback(async () => {
    if (!detail) return
    stopSpeaking()
    try {
      await intakeChatApi.discard(detail.session.id)
    } catch (err) {
      toast.error(errorMessage(err))
      return
    }
    sessionStorage.removeItem(INTAKE_SESSION_STORAGE_KEY)
    setSessionId(null)
    setDetail(null)
    setMessages([])
    setFields([])
    setIsReady(false)
    setHasOpener(false)
    setPendingDocIds([])
    setInput('')
    void qc.invalidateQueries({ queryKey: ['intake-sessions'] })
    toast.success('Черновик очищен')
  }, [detail, qc, stopSpeaking])

  // Save an inline-edited draft title (G).
  const saveTitle = useCallback(async () => {
    const t = titleDraft.trim()
    setEditingTitle(false)
    if (!detail || !t || t === detail.session.draft_title) return
    try {
      const { data } = await intakeChatApi.rename(detail.session.id, t)
      setDetail((d) => (d ? { ...d, session: data } : d))
    } catch (err) {
      toast.error(errorMessage(err))
    }
  }, [detail, titleDraft])

  // Clicking a still-missing field nudges the composer toward filling it (D).
  const nudgeField = (label: string) => {
    setSheetOpen(false)
    if (!input.trim()) setInput(`Давайте заполним «${label}»: `)
    requestAnimationFrame(() => {
      const el = inputRef.current
      if (el) {
        el.focus()
        el.setSelectionRange(el.value.length, el.value.length)
      }
    })
  }

  if (fatal) {
    return (
      <div className="animate-vfade mx-auto max-w-[560px] rounded-2xl border border-border bg-card p-8 text-center">
        <AlertCircle className="mx-auto mb-3 h-8 w-8 text-destructive" />
        <div className="text-sm">{fatal}</div>
      </div>
    )
  }

  // Resuming an existing session but its data hasn't arrived yet.
  if (!preSession && !detail) {
    return <LoadingSpinner label="Загрузка диалога…" />
  }

  const canStart = !starting && (input.trim().length > 0 || pendingDocIds.length > 0)
  const submitted = detail?.session.status === 'submitted'
  const requiredFields = fields.filter((f) => f.required)
  const fromDocCount = fields.filter((f) => f.from_document).length
  const filledRequired = requiredFields.filter((f) => !f.missing).length
  const pct = requiredFields.length
    ? Math.round((filledRequired / requiredFields.length) * 100)
    : 0
  const missingRequired = requiredFields.filter((f) => f.missing)

  // Readiness content (title, progress, field list) — shared by the desktop
  // side panel and the mobile bottom-sheet.
  const readinessInner = (
    <>
      <div className="mb-1 flex items-center gap-2">
        <Sparkles className="h-5 w-5" />
        <div className="text-sm font-semibold">Черновик заявки</div>
      </div>
      <div className="mb-3.5 text-[11.5px] text-muted-foreground">
        {detail?.session.draft_title ?? 'Заголовок появится по ходу диалога'}
      </div>
      {fromDocCount > 0 && (
        <div className="mb-3.5 flex items-center gap-1.5 rounded-lg border border-primary/20 bg-primary/5 px-2.5 py-1.5 text-[11px] font-medium text-primary">
          <FileText className="h-3 w-3 flex-none" />
          Из документа предзаполнено полей: {fromDocCount}
        </div>
      )}
      <div className="mb-1.5 flex items-center justify-between text-[11.5px]">
        <span className="font-medium text-muted-foreground">Готовность заявки</span>
        <span className="font-semibold tabular-nums">
          {filledRequired}/{requiredFields.length} · {pct}%
        </span>
      </div>
      <div className="mb-3 h-2 overflow-hidden rounded-full bg-muted">
        <div
          className={cn('h-full rounded-full transition-all', pct === 100 ? 'bg-success' : 'bg-primary')}
          style={{ width: `${pct}%` }}
        />
      </div>
      {missingRequired.length > 0 ? (
        <div className="mb-3 text-[11px] leading-snug text-muted-foreground">
          Нажмите на незаполненный пункт — подскажу вопрос.
        </div>
      ) : (
        fields.length > 0 && (
          <div className="mb-3 flex items-center gap-1.5 text-[11.5px] font-medium text-success">
            <CheckCircle2 className="h-3.5 w-3.5 flex-none" />
            Все обязательные поля собраны — можно отправлять.
          </div>
        )
      )}
      {/* Field checklist as a vertical tracker: the connector line between
          items makes the collected run readable at a glance. Completed keeps
          the page's success green (the yellow primary is too pale for a 20px
          icon on the white card). */}
      <div className="flex flex-col">
        {fields.map((f, i) => {
          const last = i === fields.length - 1
          const inner = (
            <>
              <div className="flex flex-col items-center">
                {f.missing ? (
                  <Circle className="h-5 w-5 flex-none text-muted-foreground/40" />
                ) : (
                  <CheckCircle2 className="h-5 w-5 flex-none text-success" />
                )}
                {!last && (
                  <div
                    className={cn(
                      // min-h floor: the line's height comes from the row
                      // stretching, which is less reliable inside a <button>.
                      'w-[1.5px] grow min-h-2',
                      fields[i + 1].missing ? 'bg-border' : 'bg-success/40',
                    )}
                  />
                )}
              </div>
              <div className={cn('ml-2.5 min-w-0 flex-1', !last && 'pb-3.5')}>
                <div className="text-[12px] font-semibold leading-tight">
                  {f.label}
                  {f.required && <span className="text-destructive"> *</span>}
                  {f.from_document && (
                    <span className="ml-1.5 inline-flex items-center gap-0.5 rounded-full border border-primary/30 bg-primary/10 px-1.5 py-[1px] align-middle text-[10px] font-medium text-primary">
                      <FileText className="h-2.5 w-2.5" />
                      из документа
                    </span>
                  )}
                </div>
                {f.value && (
                  <div className="mt-0.5 line-clamp-3 text-[12px] leading-snug text-muted-foreground">
                    {f.value}
                  </div>
                )}
              </div>
            </>
          )
          return f.missing && !submitted ? (
            <button
              key={f.key}
              type="button"
              onClick={() => nudgeField(f.label)}
              className="flex w-full rounded-lg text-left hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {inner}
            </button>
          ) : (
            <div key={f.key} className="flex">
              {inner}
            </div>
          )
        })}
      </div>
    </>
  )

  const ticketDoneCard = (
    <div className="rounded-2xl border-[1.5px] border-primary bg-card p-5">
      <div className="mb-2 flex items-center gap-2 text-sm font-semibold text-success">
        <CheckCircle2 className="h-5 w-5" />
        Заявка в работе
      </div>
      <div className="mb-3 rounded-[11px] bg-muted px-3.5 py-3">
        <div className="mb-[3px] text-[11px] text-muted-foreground">Номер тикета</div>
        <div className="font-mono text-[12px] font-semibold break-all">{detail?.session.ticket_id}</div>
      </div>
      <button
        type="button"
        onClick={() => navigate('/board')}
        className="w-full rounded-xl border border-input bg-card p-[11px] text-[13px] font-semibold hover:bg-muted"
      >
        К доске заявок
      </button>
    </div>
  )

  const finalizeButton = (
    <button
      type="button"
      onClick={() => void finalize()}
      disabled={!isReady || finalizing}
      className="flex w-full items-center justify-center gap-[7px] rounded-xl bg-primary p-[13px] text-[13.5px] font-bold text-primary-foreground hover:bg-primary/90 disabled:opacity-40"
    >
      {finalizing ? (
        <Loader2 className="h-[19px] w-[19px] animate-spin" />
      ) : (
        <Send className="h-[19px] w-[19px]" />
      )}
      Отправить на триаж
    </button>
  )

  // ---- Pre-session: one workspace, no separate step ----
  if (preSession) {
    return (
      <div className="animate-vfade mx-auto flex h-[calc(100dvh-200px)] w-full max-w-[760px] flex-col rounded-2xl border border-border bg-muted/40 lg:h-[calc(100vh-180px)]">
        <div ref={scrollRef} className="flex-1 space-y-4 overflow-y-auto p-5">
          {activeDrafts.length > 0 && (
            <div className="rounded-2xl border border-border bg-card p-3">
              <div className="mb-2 px-1 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                Продолжить черновик
              </div>
              <div className="flex flex-col gap-1.5">
                {activeDrafts.map((d) => (
                  <button
                    key={d.id}
                    type="button"
                    onClick={() => resumeDraft(d.id)}
                    className="flex items-center gap-3 rounded-xl border border-border bg-card p-2.5 text-left hover:border-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    <FileText className="h-4 w-4 flex-none text-muted-foreground" />
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-[13px] font-semibold">
                        {d.draft_title ?? 'Черновик без названия'}
                      </div>
                      <div className="text-[11px] text-muted-foreground">
                        Сообщений: {d.message_count} · обновлён{' '}
                        {new Date(d.updated_at).toLocaleDateString('ru-RU')}
                      </div>
                    </div>
                    <ChevronRight className="h-4 w-4 flex-none text-muted-foreground" />
                  </button>
                ))}
              </div>
            </div>
          )}
          <Bubble role="assistant" content={GREETING} />
          {!starting && (
            <div className="flex flex-wrap gap-2 pl-0 sm:pl-[42px]">
              {STARTERS.map((s) => (
                <button
                  key={s.label}
                  type="button"
                  onClick={() => seedComposer(s.seed)}
                  className="rounded-full border border-input bg-card px-3 py-1.5 text-[12px] font-medium text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  {s.label}
                </button>
              ))}
            </div>
          )}
          {starting && <TypingBubble label="Начинаю диалог…" />}
          {transcribing && <TranscribingBubble />}
        </div>
        <div className="space-y-3 p-3.5">
          {/* Attach documents (optional) */}
          <DocumentAttach
            selectedIds={pendingDocIds}
            onChange={setPendingDocIds}
            disabled={starting}
          />
          {/* Composer */}
          <PromptInput>
            {!recording && (
              <PromptInputTextarea
                ref={inputRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onSubmit={() => void send()}
                placeholder="Опишите проблему или задачу…"
                disabled={starting}
              />
            )}
            <PromptInputActions>
              {!recording && <VoiceModeToggle on={voiceMode} onToggle={toggleVoiceMode} />}
              {/* While recording the mic grows into its strip and takes this whole row. */}
              <div className="flex min-w-0 flex-1 items-center justify-end gap-1">
                <VoiceRecordButton
                  disabled={starting}
                  onTranscribingChange={setTranscribing}
                  onRecordingChange={setRecording}
                  autoStopOnSilence={voiceMode}
                  startSignal={listenSignal}
                  onTranscript={(t) => {
                    // Documents still need "Начать" to be pressed explicitly (a
                    // turn can't be sent while they're being analysed) — seed the
                    // composer instead of auto-sending in that case.
                    if (pendingDocIds.length > 0) {
                      seedComposer(input ? `${input} ${t}` : t)
                      return
                    }
                    // In voice mode this first turn creates the session; once it
                    // returns the assistant's reply we speak it, and speakReply's
                    // re-arm continues the loop in the now-active composer.
                    void startAndMaybeSend(t).then((reply) => {
                      if (voiceMode && reply) void speakReply(reply)
                    })
                  }}
                />
                {!recording && (
                  <button
                    type="button"
                    onClick={() => void send()}
                    disabled={!canStart}
                    aria-label="Начать диалог"
                    className={SEND_BUTTON_CLASS}
                  >
                    {starting ? <Loader2 className="h-5 w-5 animate-spin" /> : <ArrowUp className="h-5 w-5" />}
                  </button>
                )}
              </div>
            </PromptInputActions>
          </PromptInput>
          {pendingDocIds.length > 0 && (
            <div className="text-[11.5px] text-muted-foreground">
              К заявке приложено документов: {pendingDocIds.length}. Нажмите «Начать», и ассистент
              изучит их.
            </div>
          )}
        </div>
      </div>
    )
  }

  // ---- Active session: conversation + readiness panel ----
  return (
    <div className="animate-vfade flex h-[calc(100dvh-172px)] flex-col gap-3 lg:grid lg:h-full lg:max-w-[1240px] lg:grid-cols-[1fr_340px] lg:items-start lg:gap-[18px]">
      {/* Chat column */}
      <div className="flex min-h-0 flex-1 flex-col rounded-2xl border border-border bg-muted/40 lg:h-[calc(100vh-190px)] lg:flex-none">
        {!submitted && (
          <div className="flex items-center gap-2 rounded-t-2xl border-b border-border bg-card/70 px-4 py-2.5">
            {editingTitle ? (
              <input
                autoFocus
                value={titleDraft}
                onChange={(e) => setTitleDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault()
                    void saveTitle()
                  }
                  if (e.key === 'Escape') setEditingTitle(false)
                }}
                onBlur={() => void saveTitle()}
                maxLength={200}
                placeholder="Название черновика"
                className="min-w-0 flex-1 rounded-md border border-input bg-card px-2 py-1 text-[13px] font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              />
            ) : (
              <button
                type="button"
                onClick={() => {
                  setTitleDraft(detail?.session.draft_title ?? '')
                  setEditingTitle(true)
                }}
                className="flex min-w-0 items-center gap-1.5 text-left"
                aria-label="Переименовать черновик"
              >
                <span className="truncate text-[13px] font-semibold">
                  {detail?.session.draft_title ?? 'Черновик без названия'}
                </span>
                <Pencil className="h-3.5 w-3.5 flex-none text-muted-foreground/60" />
              </button>
            )}
            <button
              type="button"
              onClick={() => void discardSession()}
              className="ml-auto inline-flex flex-none items-center gap-1.5 rounded-lg px-2 py-1 text-[12px] font-medium text-muted-foreground hover:bg-destructive/10 hover:text-destructive focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <Trash2 className="h-3.5 w-3.5" />
              <span className="hidden sm:inline">Очистить</span>
            </button>
          </div>
        )}
        <div ref={scrollRef} className="flex-1 space-y-4 overflow-y-auto p-5">
          {analyzing ? (
            <TypingBubble label="Изучаю приложенные документы и предзаполняю черновик…" />
          ) : (
            <>
              {analysisFailed && (
                <div className="flex items-center gap-2 rounded-xl border border-border bg-muted px-3.5 py-2.5 text-[12px] text-muted-foreground">
                  <AlertCircle className="h-4 w-4 flex-none" />
                  Не удалось проанализировать приложенные документы — продолжите диалог обычным
                  образом.
                </div>
              )}
              {!hasOpener && <Bubble role="assistant" content={GREETING} />}
            </>
          )}
          {messages.map((m) => (
            <Bubble key={m.id} role={m.role} content={m.content} />
          ))}
          {thinking && <TypingBubble label="Ассистент печатает…" />}
          {transcribing && <TranscribingBubble />}
        </div>
        <div className="p-3.5">
          {submitted ? (
            <div className="flex items-center justify-center gap-2 py-2 text-[13px] font-medium text-success">
              <CheckCircle2 className="h-[18px] w-[18px]" />
              Сессия завершена — заявка отправлена на триаж
            </div>
          ) : (
            <>
              {speaking && (
                <div className="mb-2.5 flex items-center justify-between rounded-[11px] border border-primary/25 bg-primary/5 px-3 py-1.5 text-[12px] font-medium text-primary">
                  <span className="flex items-center gap-1.5">
                    <Volume2 className="h-3.5 w-3.5 flex-none animate-pulse motion-reduce:animate-none" />
                    Ассистент говорит…
                  </span>
                  <button
                    type="button"
                    onClick={stopSpeaking}
                    className="rounded-md px-2 py-0.5 font-semibold hover:bg-primary/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    Стоп
                  </button>
                </div>
              )}
              <PromptInput>
                {!recording && (
                  <PromptInputTextarea
                    ref={inputRef}
                    value={input}
                    onChange={(e) => setInput(e.target.value)}
                    onSubmit={() => void send()}
                    placeholder="Ответьте на вопрос ассистента…"
                    disabled={thinking || analyzing}
                  />
                )}
                <PromptInputActions>
                  {!recording && <VoiceModeToggle on={voiceMode} onToggle={toggleVoiceMode} />}
                  {/* While recording the mic grows into its strip and takes this whole row. */}
                  <div className="flex min-w-0 flex-1 items-center justify-end gap-1">
                    <VoiceRecordButton
                      disabled={thinking || analyzing || submitted}
                      onTranscribingChange={setTranscribing}
                      onRecordingChange={setRecording}
                      autoStopOnSilence={voiceMode}
                      startSignal={listenSignal}
                      onTranscript={(t) => {
                        // The assistant is mid-reply: sendText() would drop the text
                        // (its own thinking guard), so hand it to the composer instead.
                        if (thinking) {
                          setInput((v) => (v ? `${v} ${t}` : t))
                          return
                        }
                        void sendText(t).then((reply) => {
                          if (voiceMode && reply) void speakReply(reply)
                        })
                      }}
                    />
                    {!recording && (
                      <button
                        type="button"
                        onClick={() => void send()}
                        disabled={!input.trim() || thinking || analyzing}
                        aria-label="Отправить сообщение"
                        className={SEND_BUTTON_CLASS}
                      >
                        {thinking ? <Loader2 className="h-5 w-5 animate-spin" /> : <ArrowUp className="h-5 w-5" />}
                      </button>
                    )}
                  </div>
                </PromptInputActions>
              </PromptInput>
            </>
          )}
        </div>
      </div>

      {/* Readiness — desktop side panel */}
      <div className="hidden lg:sticky lg:top-0 lg:flex lg:flex-col lg:gap-4">
        <div className="rounded-2xl border border-border bg-card p-5">{readinessInner}</div>
        {submitted ? ticketDoneCard : finalizeButton}
        {!submitted && !isReady && (
          <div className="px-1 text-[11.5px] leading-snug text-muted-foreground">
            Кнопка станет активной, когда все обязательные поля заявки будут собраны. Решение об
            отправке всегда за вами — ассистент только готовит черновик.
          </div>
        )}
      </div>

      {/* Readiness — mobile bottom sheet (progress + action always visible) */}
      <div className="lg:hidden">
        {submitted ? (
          ticketDoneCard
        ) : (
          <>
            {sheetOpen && (
              <div className="mb-2 max-h-[46vh] overflow-y-auto rounded-2xl border border-border bg-card p-4">
                {readinessInner}
              </div>
            )}
            <div className="rounded-2xl border border-border bg-card p-3">
              <button
                type="button"
                onClick={() => setSheetOpen((o) => !o)}
                aria-expanded={sheetOpen}
                className="flex w-full items-center justify-between focus-visible:outline-none"
              >
                <span className="text-[12.5px] font-semibold">Готовность заявки</span>
                <span className="flex items-center gap-1.5 text-[12.5px] font-semibold tabular-nums">
                  {filledRequired}/{requiredFields.length} · {pct}%
                  <ChevronDown
                    className={cn('h-4 w-4 transition-transform', sheetOpen && 'rotate-180')}
                  />
                </span>
              </button>
              <div className="mb-3 mt-2 h-2 overflow-hidden rounded-full bg-muted">
                <div
                  className={cn(
                    'h-full rounded-full transition-all',
                    pct === 100 ? 'bg-success' : 'bg-primary',
                  )}
                  style={{ width: `${pct}%` }}
                />
              </div>
              {finalizeButton}
              {!isReady && (
                <div className="mt-2 text-center text-[11px] leading-snug text-muted-foreground">
                  Кнопка активируется, когда собраны все обязательные поля.
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  )
}
