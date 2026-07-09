import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { AlertCircle, Bot, ChevronRight, Send } from 'lucide-react'
import { toast } from 'sonner'
import { cn } from '@/lib/utils'
import { MANDATORY_CORE_FIELDS, templateTypeMeta } from '@/features/tickets/model'
import { ticketsApi, FREE_FORM_VERSION_ID } from '@/api/tickets'
import { templatesApi } from '@/api/templates'
import { LoadingSpinner } from '@/components/LoadingSpinner'

// Intake: template catalog (step 1) + the mandatory metadata core (step 2).
// Submits a real ticket to /v1/tickets, then calls /submit to push it to triage.

const fieldClasses =
  'w-full rounded-[11px] border border-input bg-card px-[13px] py-[11px] text-base leading-normal sm:text-[13px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring'

// Fields that render as <textarea> vs <input>
const TEXTAREA_KEYS = new Set([
  'problem',
  'expected_result',
  'as_is',
  'to_be',
  'affected_systems',
  'acceptance_criteria',
])

type CorePayload = Record<string, string>

const EMPTY_PAYLOAD: CorePayload = Object.fromEntries(
  MANDATORY_CORE_FIELDS.map((f) => [f.key, '']),
)

export default function IntakePage() {
  const navigate = useNavigate()
  const qc = useQueryClient()

  // Form state
  const [title, setTitle] = useState('')
  const [payload, setPayload] = useState<CorePayload>(EMPTY_PAYLOAD)
  const [selectedTemplateId, setSelectedTemplateId] = useState<string | null>(null)
  const [showError, setShowError] = useState(false)

  // Load templates catalog
  const templatesQ = useQuery({
    queryKey: ['templates'],
    queryFn: () => templatesApi.list(),
  })
  const templates = templatesQ.data?.data ?? []

  // Default-select the first template once loaded
  const effectiveTemplateId =
    selectedTemplateId ?? (templates.length > 0 ? templates[0].id : null)

  const setField = (key: string, value: string) =>
    setPayload((prev) => ({ ...prev, [key]: value }))

  // Validation
  const allFilled =
    title.trim().length > 0 &&
    MANDATORY_CORE_FIELDS.every((f) => payload[f.key]?.trim().length > 0)

  // Submit mutation: create ticket → submit to triage
  const submitMutation = useMutation({
    mutationFn: async () => {
      // Resolve template version id
      let templateVersionId = FREE_FORM_VERSION_ID
      if (effectiveTemplateId) {
        try {
          const detail = await templatesApi.get(effectiveTemplateId)
          const published = detail.data.versions.find((v) => v.status === 'published')
          if (published) templateVersionId = published.id
        } catch {
          // Fall back to free-form version
        }
      }

      // Build typed payload (string values only — backend accepts unknown)
      const typedPayload: Record<string, unknown> = {}
      for (const f of MANDATORY_CORE_FIELDS) {
        typedPayload[f.key] = payload[f.key]
      }

      const created = await ticketsApi.create({
        title: title.trim(),
        template_version_id: templateVersionId,
        payload: typedPayload,
      })
      const ticketId = created.data.ticket.id
      await ticketsApi.submit(ticketId)
      return ticketId
    },
    onSuccess: (ticketId: string) => {
      qc.invalidateQueries({ queryKey: ['tickets'] })
      toast.success('Заявка создана и отправлена на триаж')
      navigate('/tickets/' + ticketId)
    },
  })

  const handleSubmit = () => {
    if (!allFilled) {
      setShowError(true)
      return
    }
    setShowError(false)
    submitMutation.mutate()
  }

  return (
    <div className="animate-vfade mx-auto max-w-[820px]">
      {/* AI-assisted intake entry */}
      <button
        type="button"
        onClick={() => navigate('/intake/chat')}
        className="mb-6 flex w-full items-center gap-3.5 rounded-2xl border-[1.5px] border-primary bg-card p-4 text-left shadow-[0_4px_14px_rgba(0,0,0,.06)] hover:shadow-[0_6px_18px_rgba(0,0,0,.09)] transition-shadow"
      >
        <div className="flex h-11 w-11 flex-none items-center justify-center rounded-xl bg-primary text-primary-foreground">
          <Bot className="h-6 w-6" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="text-[13.5px] font-semibold">Собрать заявку с ИИ-ассистентом</div>
          <div className="mt-0.5 text-[12px] text-muted-foreground">
            Опишите проблему своими словами — ассистент задаст вопросы и заполнит шаблон за вас.
          </div>
        </div>
        <ChevronRight className="h-5 w-5 flex-none text-muted-foreground" />
      </button>

      {/* Step 1: template catalog */}
      <div className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
        Шаг 1 · выберите шаблон
      </div>

      {templatesQ.isLoading ? (
        <div className="mb-[26px]">
          <LoadingSpinner label="Загрузка шаблонов…" />
        </div>
      ) : templates.length === 0 ? (
        <div className="mb-[26px] rounded-[14px] border border-border bg-card p-4 text-[13px] text-muted-foreground">
          Шаблоны не найдены — заявка будет создана в свободной форме.
        </div>
      ) : (
        <div className="mb-[26px] grid grid-cols-1 gap-3 sm:grid-cols-2 md:grid-cols-3">
          {templates.map((t) => {
            const meta = templateTypeMeta(t.type)
            const Icon = meta.icon
            const selected = effectiveTemplateId === t.id
            return (
              <button
                key={t.id}
                type="button"
                onClick={() => setSelectedTemplateId(t.id)}
                className={cn(
                  'flex cursor-pointer flex-col items-start rounded-[14px] bg-card p-4 text-left transition-shadow',
                  selected
                    ? 'border-[1.5px] border-primary shadow-[0_4px_14px_rgba(0,0,0,.08)]'
                    : 'border border-border hover:border-input',
                )}
              >
                <Icon
                  className={cn(
                    'h-[22px] w-[22px]',
                    selected ? 'text-foreground' : 'text-muted-foreground',
                  )}
                />
                <span className="mt-[9px] text-[13px] font-semibold leading-tight">
                  {t.name || meta.label}
                </span>
                <span className="mt-1 text-[11.5px] leading-snug text-muted-foreground">
                  {t.description || meta.desc}
                </span>
                <span className="mt-[9px] font-mono text-[10px] text-muted-foreground">
                  {meta.doc}
                </span>
              </button>
            )
          })}
        </div>
      )}

      {/* Step 2: mandatory core form */}
      <div className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
        Шаг 2 · обязательное ядро
      </div>
      <div className="rounded-2xl border border-border bg-card p-4 sm:p-6">
        {showError && !allFilled && (
          <div className="mb-[18px] flex items-center gap-2 rounded-[10px] bg-destructive/10 px-3.5 py-[11px] text-[12.5px] font-medium text-destructive">
            <AlertCircle className="h-[18px] w-[18px] flex-none" />
            Заполните название и все обязательные поля перед отправкой.
          </div>
        )}

        {/* Title */}
        <label className="mb-1.5 block text-[12.5px] font-semibold">
          Название заявки <span className="text-destructive">*</span>
        </label>
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="Краткое и понятное название"
          className={cn(fieldClasses, 'mb-[18px]')}
        />

        {/* Mandatory core fields */}
        {MANDATORY_CORE_FIELDS.map((f) => {
          const isTextarea = TEXTAREA_KEYS.has(f.key)
          return (
            <div key={f.key} className="mb-[18px]">
              <label className="mb-1.5 block text-[12.5px] font-semibold">
                {f.label} <span className="text-destructive">*</span>
              </label>
              {isTextarea ? (
                <textarea
                  value={payload[f.key]}
                  onChange={(e) => setField(f.key, e.target.value)}
                  className={cn(fieldClasses, 'min-h-[70px] resize-y')}
                />
              ) : (
                <input
                  value={payload[f.key]}
                  onChange={(e) => setField(f.key, e.target.value)}
                  className={fieldClasses}
                />
              )}
            </div>
          )
        })}

        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={handleSubmit}
            disabled={submitMutation.isPending}
            className="flex items-center gap-[7px] rounded-xl bg-primary px-[22px] py-[13px] text-[13.5px] font-bold text-primary-foreground hover:bg-primary/90 disabled:opacity-60 disabled:cursor-not-allowed"
          >
            {submitMutation.isPending ? (
              <span
                aria-hidden
                className="inline-block h-[19px] w-[19px] rounded-full border-2 border-primary-foreground/40 border-t-primary-foreground animate-spin"
              />
            ) : (
              <Send className="h-[19px] w-[19px]" />
            )}
            Отправить на триаж
          </button>
          <span className="text-xs text-muted-foreground">
            Заявка попадёт в статус «Создан» и пройдёт триаж BA.
          </span>
        </div>
      </div>
    </div>
  )
}
