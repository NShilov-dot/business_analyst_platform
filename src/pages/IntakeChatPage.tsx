import { useCallback, useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import {
  AlertCircle,
  Bot,
  CheckCircle2,
  Circle,
  Loader2,
  Send,
  Sparkles,
} from 'lucide-react'
import { toast } from 'sonner'
import { cn } from '@/lib/utils'
import { ApiError } from '@/api/client'
import {
  intakeChatApi,
  type ChatMessage,
  type FieldState,
  type SessionDetail,
} from '@/api/intakeChat'

const GREETING =
  'Здравствуйте! Я помогу оформить бизнес-заявку. Расскажите, какая проблема ' +
  'или потребность привела вас сюда — дальше я задам уточняющие вопросы.'

function errorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    const body = err.body as { error?: { code?: string; message?: string } } | null
    if (body?.error?.code === 'LLM_UNAVAILABLE') {
      return 'ИИ-ассистент не настроен на сервере (OPENAI_API_KEY). Обратитесь к администратору.'
    }
    if (body?.error?.message) return body.error.message
  }
  return 'Что-то пошло не так. Попробуйте ещё раз.'
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
  const qc = useQueryClient()
  const [detail, setDetail] = useState<SessionDetail | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [fields, setFields] = useState<FieldState[]>([])
  const [isReady, setIsReady] = useState(false)
  const [input, setInput] = useState('')
  const [thinking, setThinking] = useState(false)
  const [finalizing, setFinalizing] = useState(false)
  const [fatal, setFatal] = useState<string | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)

  // Одна сессия на визит страницы; контекст хранится на бэкенде.
  useEffect(() => {
    let cancelled = false
    intakeChatApi
      .startSession()
      .then(({ data }) => {
        if (cancelled) return
        setDetail(data)
        setMessages(data.messages)
        setFields(data.fields)
        setIsReady(data.is_ready)
      })
      .catch((err) => !cancelled && setFatal(errorMessage(err)))
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages, thinking])

  const send = useCallback(async () => {
    const content = input.trim()
    if (!content || !detail || thinking) return
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
  }, [input, detail, thinking, messages.length])

  const finalize = useCallback(async () => {
    if (!detail || finalizing) return
    setFinalizing(true)
    try {
      const { data } = await intakeChatApi.finalize(detail.session.id)
      setDetail(data)
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

  if (fatal) {
    return (
      <div className="animate-vfade mx-auto max-w-[560px] rounded-2xl border border-border bg-card p-8 text-center">
        <AlertCircle className="mx-auto mb-3 h-8 w-8 text-destructive" />
        <div className="text-sm">{fatal}</div>
      </div>
    )
  }

  const submitted = detail?.session.status === 'submitted'
  const requiredFields = fields.filter((f) => f.required)
  const filledRequired = requiredFields.filter((f) => !f.missing).length
  const pct = requiredFields.length
    ? Math.round((filledRequired / requiredFields.length) * 100)
    : 0

  return (
    <div className="animate-vfade grid h-full max-w-[1240px] grid-cols-1 items-start gap-[18px] lg:grid-cols-[1fr_340px]">
      {/* Chat column */}
      <div className="flex h-[calc(100dvh-210px)] flex-col rounded-2xl border border-border bg-muted/40 lg:h-[calc(100vh-190px)]">
        <div ref={scrollRef} className="flex-1 space-y-4 overflow-y-auto p-5">
          <Bubble role="assistant" content={GREETING} />
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
                disabled={!detail || thinking}
                className="max-h-40 flex-1 resize-none rounded-[11px] border border-input bg-card px-[13px] py-[10px] text-base leading-relaxed sm:text-[13.5px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              />
              <button
                type="button"
                onClick={() => void send()}
                disabled={!input.trim() || !detail || thinking}
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
            {detail?.session.draft_title ?? 'Заголовок появится по ходу диалога'}
          </div>
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
                {detail?.session.ticket_id}
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
