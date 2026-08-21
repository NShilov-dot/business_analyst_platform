import { useCallback, useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import {
  AlertCircle,
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

// The active AI-intake session id is kept in sessionStorage so navigating away
// and back (or reloading) resumes the same session — the analysis is never
// lost. finalize() clears it on success.
export const INTAKE_SESSION_STORAGE_KEY = 'intake:sessionId'

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
  const scrollRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)

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
    refetchInterval: (query) =>
      query.state.data?.data.session.analysis_status === 'pending' ? 2500 : false,
  })

  useEffect(() => {
    if (sessionQuery.isError) setFatal(errorMessage(sessionQuery.error))
  }, [sessionQuery.isError, sessionQuery.error])

  useEffect(() => {
    const data = sessionQuery.data?.data
    if (data) seedFrom(data)
  }, [sessionQuery.data, seedFrom])

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages, thinking, starting])

  const analysisStatus = detail?.session.analysis_status
  const analyzing = analysisStatus === 'pending'
  const analysisFailed = analysisStatus === 'failed'

  // Create the session lazily (with any attached documents), then optionally
  // send the first user message.
  const startAndMaybeSend = useCallback(
    async (firstMessage?: string) => {
      if (starting) return
      setStarting(true)
      try {
        const { data } = await intakeChatApi.startSession(undefined, pendingDocIds)
        sessionStorage.setItem(INTAKE_SESSION_STORAGE_KEY, data.session.id)
        // Send the first message BEFORE enabling the resume query, so its very
        // first getSession sees the persisted turn and cannot clobber the local
        // messages. A message can't be sent while documents are still analysing
        // (backend rejects it), so only the no-documents path auto-sends.
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
        } else {
          seedFrom(data)
        }
        setSessionId(data.session.id) // enables the resume query + polling
      } catch (err) {
        toast.error(errorMessage(err))
      } finally {
        setStarting(false)
      }
    },
    [pendingDocIds, seedFrom, starting],
  )

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
    if (!content || !detail) return
    setInput('')
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
    } catch (err) {
      setMessages((m) => m.filter((msg) => msg.id !== optimistic.id))
      setInput(content)
      toast.error(errorMessage(err))
    } finally {
      setThinking(false)
    }
  }, [input, detail, thinking, analyzing, starting, preSession, pendingDocIds.length, messages.length, startAndMaybeSend])

  const finalize = useCallback(async () => {
    if (!detail || finalizing) return
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
  }, [detail, finalizing, qc])

  // Resume a stored draft (F).
  const resumeDraft = (id: string) => {
    sessionStorage.setItem(INTAKE_SESSION_STORAGE_KEY, id)
    setSessionId(id)
  }

  // Discard the active session and return to a fresh workspace (G).
  const discardSession = useCallback(async () => {
    if (!detail) return
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
  }, [detail, qc])

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
          Осталось собрать: {missingRequired.map((f) => f.label).join(', ')}. Нажмите на пункт —
          подскажу вопрос.
        </div>
      ) : (
        fields.length > 0 && (
          <div className="mb-3 flex items-center gap-1.5 text-[11.5px] font-medium text-success">
            <CheckCircle2 className="h-3.5 w-3.5 flex-none" />
            Все обязательные поля собраны — можно отправлять.
          </div>
        )
      )}
      <div className="flex flex-col gap-1.5">
        {fields.map((f) => {
          const inner = (
            <>
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
            </>
          )
          return f.missing && !submitted ? (
            <button
              key={f.key}
              type="button"
              onClick={() => nudgeField(f.label)}
              className="-m-1 flex gap-2.5 rounded-lg p-1 text-left hover:bg-muted/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {inner}
            </button>
          ) : (
            <div key={f.key} className="flex gap-2.5 p-1">
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
        </div>
        <div className="space-y-3 rounded-b-2xl border-t border-border bg-card p-3.5">
          {/* Attach documents (optional) */}
          <DocumentAttach
            selectedIds={pendingDocIds}
            onChange={setPendingDocIds}
            disabled={starting}
          />
          {/* Composer */}
          <div className="flex items-end gap-2.5">
            <textarea
              ref={inputRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault()
                  void send()
                }
              }}
              placeholder="Опишите проблему или задачу…"
              rows={2}
              disabled={starting}
              className="max-h-40 flex-1 resize-none rounded-[11px] border border-input bg-card px-[13px] py-[10px] text-base leading-relaxed sm:text-[13.5px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60"
            />
            <button
              type="button"
              onClick={() => void send()}
              disabled={!canStart}
              aria-label="Начать диалог"
              className="flex h-11 w-11 flex-none items-center justify-center rounded-[11px] bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-40"
            >
              {starting ? <Loader2 className="h-5 w-5 animate-spin" /> : <Send className="h-5 w-5" />}
            </button>
          </div>
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
                ref={inputRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault()
                    void send()
                  }
                }}
                placeholder="Ответьте на вопрос ассистента…"
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
