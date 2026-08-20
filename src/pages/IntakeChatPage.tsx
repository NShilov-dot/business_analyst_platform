import { useCallback, useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useLocation, useNavigate } from 'react-router-dom'
import {
  AlertCircle,
  Bot,
  CheckCircle2,
  Circle,
  FileText,
  Loader2,
  Send,
  Sparkles,
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
import { LoadingSpinner } from '@/components/LoadingSpinner'
import { INTAKE_SESSION_STORAGE_KEY } from './IntakePage'

const GREETING =
  'Здравствуйте! Я помогу оформить бизнес-заявку. Расскажите, какая проблема ' +
  'или потребность привела вас сюда — дальше я задам уточняющие вопросы.'

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

export default function IntakeChatPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const qc = useQueryClient()

  // Session is created on IntakePage; this page only resumes it by id, so
  // navigating away and back (or reloading) never loses the analysis. Read
  // once on mount (lazy initializer) — finalize() clears the storage key on
  // success, and re-reading it on every render would otherwise immediately
  // redirect back to /intake right after a successful finalize.
  const [sessionId] = useState<string | null>(
    () =>
      sessionStorage.getItem(INTAKE_SESSION_STORAGE_KEY) ??
      (location.state as { sessionId?: string } | null)?.sessionId ??
      null,
  )

  const [detail, setDetail] = useState<SessionDetail | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [fields, setFields] = useState<FieldState[]>([])
  const [isReady, setIsReady] = useState(false)
  // Server sent an opener (documents were pre-analyzed) — sticky once set, so
  // it doesn't flip once the user starts sending their own messages.
  const [hasOpener, setHasOpener] = useState(false)
  const [input, setInput] = useState('')
  const [thinking, setThinking] = useState(false)
  const [finalizing, setFinalizing] = useState(false)
  const [fatal, setFatal] = useState<string | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!sessionId) navigate('/intake', { replace: true })
  }, [sessionId, navigate])

  // Load (and, while analysis is running in the background, poll) the session.
  const sessionQuery = useQuery({
    queryKey: ['intake-chat-session', sessionId],
    queryFn: () => intakeChatApi.getSession(sessionId as string),
    enabled: !!sessionId,
    refetchOnWindowFocus: false,
    refetchInterval: (query) =>
      query.state.data?.data.session.analysis_status === 'pending' ? 2500 : false,
  })

  useEffect(() => {
    if (sessionQuery.isError) setFatal(errorMessage(sessionQuery.error))
  }, [sessionQuery.isError, sessionQuery.error])

  // Seed local chat state from the server. This effect only fires while the
  // query itself (re)fetches — the initial load, and each poll tick while
  // analysis_status is 'pending'. Once analysis settles, refetchInterval
  // stops, so later local mutations from send()/finalize() are never clobbered.
  useEffect(() => {
    const data = sessionQuery.data?.data
    if (!data) return
    setDetail(data)
    setMessages(data.messages)
    setHasOpener(data.messages.length > 0)
    setFields(data.fields)
    setIsReady(data.is_ready)
  }, [sessionQuery.data])

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages, thinking])

  const analysisStatus = detail?.session.analysis_status
  const analyzing = analysisStatus === 'pending'
  const analysisFailed = analysisStatus === 'failed'

  const send = useCallback(async () => {
    const content = input.trim()
    if (!content || !detail || thinking || analyzing) return
    setInput('')
    setThinking(true)
    // Оптимистично показываем реплику пользователя
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
    } catch (err) {
      setMessages((m) => m.filter((msg) => msg.id !== optimistic.id))
      setInput(content)
      toast.error(errorMessage(err))
    } finally {
      setThinking(false)
    }
  }, [input, detail, thinking, analyzing, messages.length])

  const finalize = useCallback(async () => {
    if (!detail || finalizing) return
    setFinalizing(true)
    try {
      const { data } = await intakeChatApi.finalize(detail.session.id)
      setDetail(data)
      sessionStorage.removeItem(INTAKE_SESSION_STORAGE_KEY)
      // The finalize created + submitted a real ticket — drop stale board/detail
      // caches so it shows up immediately (was invisible for up to staleTime=30s).
      void qc.invalidateQueries({ queryKey: ['tickets'] })
      toast.success(`Заявка создана и отправлена на триаж (${data.session.ticket_id})`)
    } catch (err) {
      toast.error(errorMessage(err))
    } finally {
      setFinalizing(false)
    }
  }, [detail, finalizing, qc])

  if (!sessionId) return null // redirecting to /intake

  if (fatal) {
    return (
      <div className="animate-vfade mx-auto max-w-[560px] rounded-2xl border border-border bg-card p-8 text-center">
        <AlertCircle className="mx-auto mb-3 h-8 w-8 text-destructive" />
        <div className="text-sm">{fatal}</div>
      </div>
    )
  }

  if (!detail) {
    return <LoadingSpinner label="Загрузка диалога…" />
  }

  const submitted = detail.session.status === 'submitted'
  const requiredFields = fields.filter((f) => f.required)
  const fromDocCount = fields.filter((f) => f.from_document).length
  const filledRequired = requiredFields.filter((f) => !f.missing).length
  const pct = requiredFields.length
    ? Math.round((filledRequired / requiredFields.length) * 100)
    : 0

  return (
    <div className="animate-vfade grid h-full max-w-[1240px] grid-cols-1 items-start gap-[18px] lg:grid-cols-[1fr_340px]">
      {/* Chat column */}
      <div className="flex h-[calc(100dvh-210px)] flex-col rounded-2xl border border-border bg-muted/40 lg:h-[calc(100vh-190px)]">
        <div ref={scrollRef} className="flex-1 space-y-4 overflow-y-auto p-5">
          {analyzing ? (
            <div className="flex gap-2.5">
              <div className="flex h-8 w-8 flex-none items-center justify-center rounded-[9px] bg-primary text-primary-foreground">
                <Bot className="h-[18px] w-[18px]" />
              </div>
              <div className="flex items-center gap-2 rounded-2xl rounded-bl-md border border-border bg-card px-4 py-2.5 text-[13px] text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" />
                Изучаю приложенные документы и предзаполняю черновик…
              </div>
            </div>
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
          {thinking && (
            <div className="flex items-center gap-2.5">
              <div className="flex h-8 w-8 flex-none items-center justify-center rounded-[9px] bg-primary text-primary-foreground">
                <Bot className="h-[18px] w-[18px]" />
              </div>
              <div className="flex items-center gap-2 rounded-2xl rounded-bl-md border border-border bg-card px-4 py-2.5 text-[13px] text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" />
                Ассистент печатает…
              </div>
            </div>
          )}
        </div>
        <div className="border-t border-border bg-card p-3.5 rounded-b-2xl">
          {submitted ? (
            <div className="flex items-center justify-center gap-2 py-2 text-[13px] font-medium text-success">
              <CheckCircle2 className="h-[18px] w-[18px]" />
              Сессия завершена — заявка отправлена на триаж
            </div>
          ) : (
            <div className="flex items-end gap-2.5">
              <textarea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault()
                    void send()
                  }
                }}
                placeholder="Опишите проблему или ответьте на вопрос ассистента…"
                rows={2}
                disabled={thinking || analyzing}
                className="max-h-40 flex-1 resize-none rounded-[11px] border border-input bg-card px-[13px] py-[10px] text-base leading-relaxed sm:text-[13.5px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60"
              />
              <button
                type="button"
                onClick={() => void send()}
                disabled={!input.trim() || thinking || analyzing}
                aria-label="Отправить сообщение"
                className="flex h-11 w-11 flex-none items-center justify-center rounded-[11px] bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-40"
              >
                <Send className="h-5 w-5" />
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Draft panel */}
      <div className="flex flex-col gap-4 lg:sticky lg:top-0">
        <div className="rounded-2xl border border-border bg-card p-5">
          <div className="mb-1 flex items-center gap-2">
            <Sparkles className="h-5 w-5" />
            <div className="text-sm font-semibold">Черновик заявки</div>
          </div>
          <div className="mb-3.5 text-[11.5px] text-muted-foreground">
            {detail.session.draft_title ?? 'Заголовок появится по ходу диалога'}
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
          <div className="mb-4 h-2 overflow-hidden rounded-full bg-muted">
            <div
              className={cn(
                'h-full rounded-full transition-all',
                pct === 100 ? 'bg-success' : 'bg-primary',
              )}
              style={{ width: `${pct}%` }}
            />
          </div>
          <div className="flex flex-col gap-3">
            {fields.map((f) => (
              <div key={f.key} className="flex gap-2.5">
                {f.missing ? (
                  <Circle className="mt-0.5 h-4 w-4 flex-none text-muted-foreground/40" />
                ) : (
                  <CheckCircle2 className="mt-0.5 h-4 w-4 flex-none text-success" />
                )}
                <div className="min-w-0">
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
              </div>
            ))}
          </div>
        </div>

        {submitted ? (
          <div className="rounded-2xl border-[1.5px] border-primary bg-card p-5">
            <div className="mb-2 flex items-center gap-2 text-sm font-semibold text-success">
              <CheckCircle2 className="h-5 w-5" />
              Заявка в работе
            </div>
            <div className="mb-3 rounded-[11px] bg-muted px-3.5 py-3">
              <div className="mb-[3px] text-[11px] text-muted-foreground">Номер тикета</div>
              <div className="font-mono text-[12px] font-semibold break-all">
                {detail.session.ticket_id}
              </div>
            </div>
            <button
              type="button"
              onClick={() => navigate('/board')}
              className="w-full rounded-xl border border-input bg-card p-[11px] text-[13px] font-semibold hover:bg-muted"
            >
              К доске заявок
            </button>
          </div>
        ) : (
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
        )}
        {!submitted && !isReady && (
          <div className="px-1 text-[11.5px] leading-snug text-muted-foreground">
            Кнопка станет активной, когда все обязательные поля заявки будут собраны.
            Решение об отправке всегда за вами — ассистент только готовит черновик.
          </div>
        )}
      </div>
    </div>
  )
}
