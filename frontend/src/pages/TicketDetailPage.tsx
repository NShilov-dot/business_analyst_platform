import { useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import {
  ArrowLeft,
  Award,
  CheckCircle2,
  ShieldCheck,
  ThumbsUp,
  Send,
  XCircle,
  RotateCcw,
  PlayCircle,
  StopCircle,
  ClipboardCheck,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { ticketsApi } from '@/api/tickets'
import type { RejectionReason, TriageAcceptInput } from '@/api/tickets'
import { departmentsApi } from '@/api/departments'
import { directoryApi } from '@/api/directory'
import { useAuth } from '@/auth/AuthProvider'
import { LoadingSpinner } from '@/components/LoadingSpinner'
import { UserCombobox } from '@/components/UserCombobox'
import {
  STATUS_META,
  MANDATORY_CORE_FIELDS,
  priorityMeta,
  fieldLabel,
} from '@/features/tickets/model'
import { ruDate, ruDateTime, shortSubject } from '@/features/tickets/format'

// ─── Helpers ────────────────────────────────────────────────────────────────

const REJECTION_REASON_LABELS: Record<RejectionReason, string> = {
  duplicate: 'Дубликат',
  irrelevant: 'Не релевантно',
  unjustified: 'Не обосновано',
}

// ─── Small sub-components ────────────────────────────────────────────────────

function SectionCard({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-2xl border border-border bg-card px-4 py-[22px] sm:px-6">
      {children}
    </div>
  )
}

function ActionCard({
  children,
  highlight,
}: {
  children: React.ReactNode
  highlight?: boolean
}) {
  return (
    <div
      className={cn(
        'rounded-2xl bg-card p-5',
        highlight
          ? 'border-[1.5px] border-primary shadow-[0_6px_22px_rgba(0,0,0,.07)]'
          : 'border border-border',
      )}
    >
      {children}
    </div>
  )
}

function PrimaryBtn({
  onClick,
  disabled,
  children,
}: {
  onClick?: () => void
  disabled?: boolean
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="mb-[9px] flex w-full items-center justify-center gap-[7px] rounded-xl bg-primary p-[13px] text-[13.5px] font-bold text-primary-foreground hover:bg-primary/90 disabled:opacity-50 disabled:cursor-not-allowed"
    >
      {children}
    </button>
  )
}

function SecondaryBtn({
  onClick,
  disabled,
  children,
}: {
  onClick?: () => void
  disabled?: boolean
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="w-full rounded-xl border border-input bg-card p-[11px] text-[13px] font-semibold hover:bg-muted disabled:opacity-50 disabled:cursor-not-allowed"
    >
      {children}
    </button>
  )
}

function DestructiveBtn({
  onClick,
  disabled,
  children,
}: {
  onClick?: () => void
  disabled?: boolean
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="w-full rounded-xl border border-destructive/40 bg-destructive/5 p-[11px] text-[13px] font-semibold text-destructive hover:bg-destructive/10 disabled:opacity-50 disabled:cursor-not-allowed"
    >
      {children}
    </button>
  )
}

function Label({ children }: { children: React.ReactNode }) {
  return (
    <label className="mb-1 block text-[11.5px] font-semibold text-muted-foreground uppercase tracking-wide">
      {children}
    </label>
  )
}

function Input({
  value,
  onChange,
  placeholder,
  required,
}: {
  value: string
  onChange: (v: string) => void
  placeholder?: string
  required?: boolean
}) {
  return (
    <input
      type="text"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      required={required}
      className="w-full rounded-lg border border-input bg-background px-3 py-2 text-[13px] focus:outline-none focus:ring-2 focus:ring-primary/40"
    />
  )
}

function Textarea({
  value,
  onChange,
  placeholder,
  required,
  rows = 3,
}: {
  value: string
  onChange: (v: string) => void
  placeholder?: string
  required?: boolean
  rows?: number
}) {
  return (
    <textarea
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      required={required}
      rows={rows}
      className="w-full rounded-lg border border-input bg-background px-3 py-2 text-[13px] focus:outline-none focus:ring-2 focus:ring-primary/40 resize-none"
    />
  )
}

function Select({
  value,
  onChange,
  options,
  placeholder,
  required,
}: {
  value: string
  onChange: (v: string) => void
  options: { value: string; label: string }[]
  placeholder?: string
  required?: boolean
}) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      required={required}
      className="w-full rounded-lg border border-input bg-background px-3 py-2 text-[13px] focus:outline-none focus:ring-2 focus:ring-primary/40"
    >
      {placeholder && (
        <option value="" disabled>
          {placeholder}
        </option>
      )}
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  )
}

function MutedNote({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <p className={cn('text-[12.5px] text-muted-foreground leading-relaxed', className)}>
      {children}
    </p>
  )
}

function InlineFormWrapper({
  title,
  onClose,
  children,
}: {
  title: string
  onClose: () => void
  children: React.ReactNode
}) {
  return (
    <div className="mt-3 rounded-xl border border-border bg-muted/40 p-4">
      <div className="mb-3 flex items-center justify-between">
        <span className="text-[13px] font-semibold">{title}</span>
        <button
          type="button"
          onClick={onClose}
          className="text-[11px] text-muted-foreground hover:text-foreground"
        >
          Отмена
        </button>
      </div>
      {children}
    </div>
  )
}

// ─── Main component ──────────────────────────────────────────────────────────

export default function TicketDetailPage() {
  const { id } = useParams<{ id: string }>()
  const nav = useNavigate()
  const { user } = useAuth()
  const qc = useQueryClient()

  // ── Queries ──────────────────────────────────────────────────────────────

  const detailQ = useQuery({
    queryKey: ['tickets', id],
    queryFn: () => ticketsApi.get(id!),
    enabled: !!id,
  })

  const transQ = useQuery({
    queryKey: ['tickets', id, 'transitions'],
    queryFn: () => ticketsApi.transitions(id!),
    enabled: !!id,
  })

  const deptsQ = useQuery({
    queryKey: ['departments'],
    queryFn: () => departmentsApi.list(),
  })

  const dirQ = useQuery({
    queryKey: ['directory', 'users'],
    queryFn: () => directoryApi.users(undefined, 200),
  })

  const detail = detailQ.data?.data
  const transitions = transQ.data?.data ?? []
  const depts = deptsQ.data?.data ?? []

  // Resolve a Keycloak subject to a human name via the directory (falls back to
  // 'Вы' for the current user, else a short id).
  const nameOf = (subject: string): string => {
    if (subject === user.subject) return 'Вы'
    const u = dirQ.data?.data.find((x) => x.subject === subject)
    return u?.full_name ?? shortSubject(subject, user.subject)
  }

  // ── Local form state ─────────────────────────────────────────────────────

  // Which inline form is open
  const [openForm, setOpenForm] = useState<string | null>(null)

  // Submit (created -> triage)
  // no extra fields needed

  // Triage accept form
  const [triageDeptId, setTriageDeptId] = useState('')
  const [triageBizOwner, setTriageBizOwner] = useState<string | null>(null)
  const [triageExecutor, setTriageExecutor] = useState<string | null>(null)
  const [triagePriority, setTriagePriority] = useState('')
  const [triageComment, setTriageComment] = useState('')

  // Return for refinement
  const [returnComment, setReturnComment] = useState('')

  // Reject form
  const [rejectComment, setRejectComment] = useState('')
  const [rejectReason, setRejectReason] = useState('')
  const [rejectDuplicateId, setRejectDuplicateId] = useState('')

  // Spec approval form. The ticket itself is the ТЗ — no external spec link is
  // collected; the backend self-references the ticket + intake version.
  // `agreed_with_subject` is NOT a free choice: the domain requires it to equal
  // the business_owner assigned at triage, so it is derived, not picked.
  const [specComment, setSpecComment] = useState('')

  // Request acceptance form
  const [changesSummary, setChangesSummary] = useState('')

  // Formal DoD checklist
  const [dodAcMet, setDodAcMet] = useState(false)
  const [dodBeforeAfter, setDodBeforeAfter] = useState(false)
  const [dodProtocol, setDodProtocol] = useState(false)
  const [dodNoP0P1, setDodNoP0P1] = useState(false)
  const [dodComment, setDodComment] = useState('')

  // Business value comment
  const [valueComment, setValueComment] = useState('')

  // Return to work
  const [returnToWorkComment, setReturnToWorkComment] = useState('')

  // ── Mutations ─────────────────────────────────────────────────────────────

  function invalidate() {
    void qc.invalidateQueries({ queryKey: ['tickets'] })
  }

  const submitMut = useMutation({
    mutationFn: () => ticketsApi.submit(id!),
    onSuccess: () => {
      invalidate()
      toast.success('Заявка отправлена на триаж')
    },
  })

  const triageAcceptMut = useMutation({
    mutationFn: (body: TriageAcceptInput) => ticketsApi.triageAccept(id!, body),
    onSuccess: () => {
      invalidate()
      toast.success('Заявка принята на триаж')
      setOpenForm(null)
      setTriageDeptId('')
      setTriageBizOwner(null)
      setTriageExecutor(null)
      setTriagePriority('')
      setTriageComment('')
    },
  })

  const returnForRefinementMut = useMutation({
    mutationFn: (comment: string) => ticketsApi.returnForRefinement(id!, comment),
    onSuccess: () => {
      invalidate()
      toast.success('Заявка возвращена на доработку')
      setOpenForm(null)
      setReturnComment('')
    },
  })

  const rejectMut = useMutation({
    mutationFn: () =>
      ticketsApi.reject(id!, {
        comment: rejectComment,
        rejection_reason: (rejectReason as RejectionReason) || undefined,
        duplicate_of_ticket_id: rejectDuplicateId || undefined,
      }),
    onSuccess: () => {
      invalidate()
      toast.success('Заявка отклонена')
      setOpenForm(null)
      setRejectComment('')
      setRejectReason('')
      setRejectDuplicateId('')
    },
  })

  const attestSpecMut = useMutation({
    mutationFn: () => {
      // agreed_with_subject MUST equal the active business_owner (assigned at
      // triage) — derive it from the freshest detail rather than asking the BA.
      const bo = detailQ.data?.data.assignments.find(
        (a) => a.role === 'business_owner' && a.unassigned_at === null,
      )?.subject
      return ticketsApi.attestSpecApproval(id!, {
        agreed_with_subject: bo ?? '',
        comment: specComment || undefined,
      })
    },
    onSuccess: () => {
      invalidate()
      toast.success('ТЗ подписано')
      setOpenForm(null)
      setSpecComment('')
    },
  })

  const startWorkMut = useMutation({
    mutationFn: () => ticketsApi.startWork(id!),
    onSuccess: () => {
      invalidate()
      toast.success('Заявка переведена в работу')
    },
  })

  const finishWorkMut = useMutation({
    mutationFn: () => ticketsApi.finishWork(id!),
    onSuccess: () => {
      invalidate()
      toast.success('Работа завершена')
    },
  })

  const requestAcceptanceMut = useMutation({
    mutationFn: () => ticketsApi.requestAcceptance(id!, changesSummary.trim()),
    onSuccess: () => {
      invalidate()
      toast.success('Запрос на приёмку отправлен')
      setOpenForm(null)
      setChangesSummary('')
    },
  })

  const attestFormalDodMut = useMutation({
    mutationFn: () =>
      ticketsApi.attestFormalDod(
        id!,
        {
          dod_ac_met: dodAcMet,
          dod_before_after: dodBeforeAfter,
          dod_protocol: dodProtocol,
          dod_no_p0_p1: dodNoP0P1,
        },
        dodComment || undefined,
      ),
    onSuccess: () => {
      invalidate()
      toast.success('Формальный DoD подписан')
      setOpenForm(null)
      setDodAcMet(false)
      setDodBeforeAfter(false)
      setDodProtocol(false)
      setDodNoP0P1(false)
      setDodComment('')
    },
  })

  const attestBusinessValueMut = useMutation({
    mutationFn: () => ticketsApi.attestBusinessValue(id!, valueComment || undefined),
    onSuccess: () => {
      invalidate()
      toast.success('Ценность подтверждена')
      setOpenForm(null)
      setValueComment('')
    },
  })

  const returnToWorkMut = useMutation({
    mutationFn: () => ticketsApi.returnToWork(id!, returnToWorkComment.trim()),
    onSuccess: () => {
      invalidate()
      toast.success('Заявка возвращена в работу')
      setOpenForm(null)
      setReturnToWorkComment('')
    },
  })

  const closeMut = useMutation({
    mutationFn: () => ticketsApi.close(id!),
    onSuccess: () => {
      invalidate()
      toast.success('Заявка закрыта')
    },
  })

  // ── Loading / error states ────────────────────────────────────────────────

  if (detailQ.isLoading) {
    return <LoadingSpinner label="Загрузка заявки…" />
  }

  if (detailQ.isError || !detail) {
    return (
      <div className="animate-vfade max-w-[1180px]">
        <button
          type="button"
          onClick={() => nav('/board')}
          className="mb-3.5 inline-flex items-center gap-[5px] text-[13px] font-medium text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="h-[18px] w-[18px]" />К доске заявок
        </button>
        <SectionCard>
          <p className="text-[14px] text-muted-foreground">Заявка не найдена.</p>
        </SectionCard>
      </div>
    )
  }

  // ── Derived values ────────────────────────────────────────────────────────

  const ticket = detail.ticket
  const submission = detail.current_submission
  const assignments = detail.assignments
  const attestations = detail.attestations
  const triageDecisions = detail.triage_decisions

  const statusMeta = STATUS_META[ticket.status]
  const pMeta = priorityMeta(ticket.priority)

  const deptMap = new Map(depts.map((d) => [d.id, d.name]))
  const deptName = ticket.department_id ? (deptMap.get(ticket.department_id) ?? '—') : '—'

  const roles = user.roles
  const isBa = roles.includes('ba')
  const isAdmin = roles.includes('tenant_admin') || roles.includes('platform_admin')
  const isAuthor = ticket.author_id === user.subject

  const activeBusinessOwner = assignments.find(
    (a) => a.role === 'business_owner' && a.unassigned_at === null,
  )?.subject
  const isBusinessOwner = activeBusinessOwner === user.subject
  const isExecutor =
    assignments.find((a) => a.role === 'executor' && a.unassigned_at === null)?.subject ===
    user.subject

  const cycle = ticket.acceptance_cycle
  const hasSpecApproved = attestations.some((a) => a.kind === 'spec_approved')
  const hasFormalDod = attestations.some(
    (a) => a.kind === 'formal_dod' && a.acceptance_cycle === cycle,
  )
  const hasBusinessValue = attestations.some(
    (a) => a.kind === 'business_value' && a.acceptance_cycle === cycle,
  )

  const status = ticket.status

  // Fields to show in the definition list (skip as_is / to_be and empty values)
  const AS_IS_TO_BE_KEYS = new Set(['as_is', 'to_be'])
  const payloadEntries = submission
    ? MANDATORY_CORE_FIELDS.filter(
        (f) => !AS_IS_TO_BE_KEYS.has(f.key) && submission.payload[f.key] != null && submission.payload[f.key] !== '',
      ).map((f) => ({ key: f.key, label: fieldLabel(f.key), value: String(submission.payload[f.key]) }))
    : []

  // ── Reject form (reused in triage and acceptance) ─────────────────────────

  function RejectForm() {
    return (
      <InlineFormWrapper title="Отклонить заявку" onClose={() => setOpenForm(null)}>
        <div className="flex flex-col gap-3">
          <div>
            <Label>Комментарий *</Label>
            <Textarea
              value={rejectComment}
              onChange={setRejectComment}
              placeholder="Причина отклонения"
              required
            />
          </div>
          <div>
            <Label>Причина (опционально)</Label>
            <Select
              value={rejectReason}
              onChange={setRejectReason}
              placeholder="— выберите —"
              options={Object.entries(REJECTION_REASON_LABELS).map(([value, label]) => ({
                value,
                label,
              }))}
            />
          </div>
          {rejectReason === 'duplicate' && (
            <div>
              <Label>ID дубликата (опционально)</Label>
              <Input
                value={rejectDuplicateId}
                onChange={setRejectDuplicateId}
                placeholder="UUID заявки-оригинала"
              />
            </div>
          )}
          <PrimaryBtn
            onClick={() => {
              if (!rejectComment.trim()) return
              rejectMut.mutate()
            }}
            disabled={rejectMut.isPending || !rejectComment.trim()}
          >
            <XCircle className="h-[17px] w-[17px]" />
            Отклонить
          </PrimaryBtn>
        </div>
      </InlineFormWrapper>
    )
  }

  // ── Workflow cockpit ──────────────────────────────────────────────────────

  function WorkflowCockpit() {
    if (status === 'created') {
      if (!(isAuthor || isBa || isAdmin)) {
        return <MutedNote>Ожидает отправки автором заявки.</MutedNote>
      }
      return (
        <ActionCard>
          <div className="mb-1 flex items-center gap-2">
            <Send className="h-5 w-5 text-muted-foreground" />
            <div className="text-sm font-semibold">Отправить на триаж</div>
          </div>
          <MutedNote className="mb-3">Заявка создана, но ещё не отправлена на рассмотрение.</MutedNote>
          <PrimaryBtn onClick={() => submitMut.mutate()} disabled={submitMut.isPending}>
            <Send className="h-[17px] w-[17px]" />
            Отправить на триаж
          </PrimaryBtn>
        </ActionCard>
      )
    }

    if (status === 'triage') {
      if (!(isBa || isAdmin)) {
        return <MutedNote>Заявка на триаже. Ожидает решения бизнес-аналитика.</MutedNote>
      }
      return (
        <ActionCard>
          <div className="mb-1 flex items-center gap-2">
            <ClipboardCheck className="h-5 w-5 text-muted-foreground" />
            <div className="text-sm font-semibold">Триаж</div>
          </div>
          <MutedNote className="mb-3">Проверьте заявку и примите решение.</MutedNote>

          {openForm === 'triage-accept' ? (
            <InlineFormWrapper title="Принять заявку" onClose={() => setOpenForm(null)}>
              <div className="flex flex-col gap-3">
                <div>
                  <Label>Отдел *</Label>
                  <Select
                    value={triageDeptId}
                    onChange={setTriageDeptId}
                    placeholder="— выберите отдел —"
                    required
                    options={depts.map((d) => ({ value: d.id, label: d.name }))}
                  />
                </div>
                <div>
                  <Label>Бизнес-заказчик *</Label>
                  <p className="mb-1 text-[11px] text-muted-foreground">Поиск по имени</p>
                  <UserCombobox
                    value={triageBizOwner}
                    onChange={setTriageBizOwner}
                    placeholder="Начните вводить имя бизнес-заказчика…"
                    required
                  />
                </div>
                <div>
                  <Label>Исполнитель (опционально)</Label>
                  <UserCombobox
                    value={triageExecutor}
                    onChange={setTriageExecutor}
                    placeholder="Имя исполнителя (опционально)…"
                  />
                </div>
                <div>
                  <Label>Приоритет (опционально)</Label>
                  <Select
                    value={triagePriority}
                    onChange={setTriagePriority}
                    placeholder="— без приоритета —"
                    options={[
                      { value: 'low', label: 'Низкий' },
                      { value: 'medium', label: 'Средний' },
                      { value: 'high', label: 'Высокий' },
                      { value: 'critical', label: 'Критичный' },
                    ]}
                  />
                </div>
                <div>
                  <Label>Комментарий (опционально)</Label>
                  <Textarea
                    value={triageComment}
                    onChange={setTriageComment}
                    placeholder="Любые заметки"
                  />
                </div>
                <PrimaryBtn
                  onClick={() => {
                    if (!triageDeptId || !triageBizOwner) return
                    const body: TriageAcceptInput = {
                      department_id: triageDeptId,
                      business_owner_subject: triageBizOwner,
                      executor_subject: triageExecutor ?? undefined,
                      priority: (triagePriority as TriageAcceptInput['priority']) || undefined,
                      comment: triageComment.trim() || undefined,
                    }
                    triageAcceptMut.mutate(body)
                  }}
                  disabled={triageAcceptMut.isPending || !triageDeptId || !triageBizOwner}
                >
                  <CheckCircle2 className="h-[17px] w-[17px]" />
                  Принять заявку
                </PrimaryBtn>
              </div>
            </InlineFormWrapper>
          ) : openForm === 'triage-return' ? (
            <InlineFormWrapper title="Вернуть на доработку" onClose={() => setOpenForm(null)}>
              <div className="flex flex-col gap-3">
                <div>
                  <Label>Комментарий *</Label>
                  <Textarea
                    value={returnComment}
                    onChange={setReturnComment}
                    placeholder="Что нужно доработать"
                    required
                  />
                </div>
                <PrimaryBtn
                  onClick={() => {
                    if (!returnComment.trim()) return
                    returnForRefinementMut.mutate(returnComment.trim())
                  }}
                  disabled={returnForRefinementMut.isPending || !returnComment.trim()}
                >
                  <RotateCcw className="h-[17px] w-[17px]" />
                  Вернуть на доработку
                </PrimaryBtn>
              </div>
            </InlineFormWrapper>
          ) : openForm === 'triage-reject' ? (
            RejectForm()
          ) : (
            <div className="flex flex-col gap-2">
              <PrimaryBtn onClick={() => setOpenForm('triage-accept')}>
                <CheckCircle2 className="h-[17px] w-[17px]" />
                Принять
              </PrimaryBtn>
              <SecondaryBtn onClick={() => setOpenForm('triage-return')}>
                <span className="flex items-center justify-center gap-2">
                  <RotateCcw className="h-[16px] w-[16px]" />
                  Вернуть на доработку
                </span>
              </SecondaryBtn>
              <DestructiveBtn onClick={() => setOpenForm('triage-reject')}>
                <span className="flex items-center justify-center gap-2">
                  <XCircle className="h-[16px] w-[16px]" />
                  Отклонить
                </span>
              </DestructiveBtn>
            </div>
          )}
        </ActionCard>
      )
    }

    if (status === 'spec_approval') {
      if (!(isBa || isAdmin || isExecutor)) {
        return <MutedNote>Ожидает подписания ТЗ бизнес-аналитиком.</MutedNote>
      }
      return (
        <ActionCard>
          <div className="mb-1 flex items-center gap-2">
            <ShieldCheck className="h-5 w-5 text-muted-foreground" />
            <div className="text-sm font-semibold">ТЗ / Согласование</div>
          </div>

          {hasSpecApproved ? (
            <div className="mb-3 flex items-center gap-2 text-[12.5px] font-semibold text-success">
              <CheckCircle2 className="h-[17px] w-[17px]" />
              ТЗ подписано
            </div>
          ) : isBa || isAdmin ? (
            openForm === 'spec-approval' ? (
              <InlineFormWrapper title="Подписать ТЗ" onClose={() => setOpenForm(null)}>
                <div className="flex flex-col gap-3">
                  <MutedNote>
                    Согласуется данная заявка как ТЗ — подписывается её текущая версия.
                  </MutedNote>
                  <div>
                    <Label>Согласовано с</Label>
                    {activeBusinessOwner ? (
                      <div className="rounded-lg border border-input bg-muted/40 px-3 py-2 text-[13px] font-medium">
                        {nameOf(activeBusinessOwner)}
                        <span className="ml-1.5 text-[11.5px] font-normal text-muted-foreground">
                          · бизнес-заказчик заявки
                        </span>
                      </div>
                    ) : (
                      <MutedNote>
                        У заявки нет активного бизнес-заказчика — назначьте его на этапе триажа.
                      </MutedNote>
                    )}
                  </div>
                  <div>
                    <Label>Комментарий (опционально)</Label>
                    <Textarea
                      value={specComment}
                      onChange={setSpecComment}
                      placeholder="Любые примечания"
                    />
                  </div>
                  <PrimaryBtn
                    onClick={() => {
                      if (!activeBusinessOwner) return
                      attestSpecMut.mutate()
                    }}
                    disabled={attestSpecMut.isPending || !activeBusinessOwner}
                  >
                    <ShieldCheck className="h-[17px] w-[17px]" />
                    Подписать ТЗ
                  </PrimaryBtn>
                </div>
              </InlineFormWrapper>
            ) : (
              <div className="mb-3">
                <SecondaryBtn onClick={() => setOpenForm('spec-approval')}>
                  <span className="flex items-center justify-center gap-2">
                    <ShieldCheck className="h-[16px] w-[16px]" />
                    Подписать ТЗ
                  </span>
                </SecondaryBtn>
              </div>
            )
          ) : (
            <MutedNote className="mb-3">Ожидает подписания BA.</MutedNote>
          )}

          {(isBa || isAdmin || isExecutor) && (
            <>
              <PrimaryBtn
                onClick={() => startWorkMut.mutate()}
                disabled={startWorkMut.isPending || !hasSpecApproved}
              >
                <PlayCircle className="h-[17px] w-[17px]" />
                В работу
              </PrimaryBtn>
              {!hasSpecApproved && (
                <MutedNote className="mt-2">
                  Заявка уходит «В работу» только после подписания ТЗ.
                </MutedNote>
              )}
            </>
          )}
        </ActionCard>
      )
    }

    if (status === 'in_progress') {
      if (!(isExecutor || isBa || isAdmin)) {
        return <MutedNote>Заявка в работе. Ожидает завершения исполнителем.</MutedNote>
      }
      return (
        <ActionCard>
          <div className="mb-1 flex items-center gap-2">
            <PlayCircle className="h-5 w-5 text-muted-foreground" />
            <div className="text-sm font-semibold">В работе</div>
          </div>
          <MutedNote className="mb-3">После завершения работ зафиксируйте изменения.</MutedNote>
          <PrimaryBtn
            onClick={() => finishWorkMut.mutate()}
            disabled={finishWorkMut.isPending}
          >
            <StopCircle className="h-[17px] w-[17px]" />
            Завершить работу
          </PrimaryBtn>
        </ActionCard>
      )
    }

    if (status === 'change_capture') {
      if (!(isExecutor || isBa || isAdmin)) {
        return <MutedNote>Ожидает фиксации изменений («было / стало») исполнителем.</MutedNote>
      }
      return (
        <ActionCard>
          <div className="mb-1 flex items-center gap-2">
            <ClipboardCheck className="h-5 w-5 text-muted-foreground" />
            <div className="text-sm font-semibold">Фиксация изменений</div>
          </div>
          <MutedNote className="mb-3">Опишите, что было сделано, и запросите приёмку.</MutedNote>

          {openForm === 'request-acceptance' ? (
            <InlineFormWrapper title="Запросить приёмку" onClose={() => setOpenForm(null)}>
              <div className="flex flex-col gap-3">
                <div>
                  <Label>Описание изменений *</Label>
                  <Textarea
                    value={changesSummary}
                    onChange={setChangesSummary}
                    placeholder="Что было сделано («было / стало»)"
                    required
                    rows={4}
                  />
                </div>
                <PrimaryBtn
                  onClick={() => {
                    if (!changesSummary.trim()) return
                    requestAcceptanceMut.mutate()
                  }}
                  disabled={requestAcceptanceMut.isPending || !changesSummary.trim()}
                >
                  <Send className="h-[17px] w-[17px]" />
                  Запросить приёмку
                </PrimaryBtn>
              </div>
            </InlineFormWrapper>
          ) : (
            <PrimaryBtn onClick={() => setOpenForm('request-acceptance')}>
              <Send className="h-[17px] w-[17px]" />
              Запросить приёмку
            </PrimaryBtn>
          )}
        </ActionCard>
      )
    }

    if (status === 'acceptance') {
      return (
        <div className="flex flex-col gap-4">
          {/* Gate 1 — Formal DoD */}
          <ActionCard>
            <div className="mb-1 flex items-center gap-2">
              <ShieldCheck className="h-5 w-5 text-success" />
              <div className="text-sm font-semibold">Гейт 1 · Формальный</div>
            </div>
            <div className="mb-3 text-[11.5px] text-muted-foreground">Решает BA · Definition of Done</div>

            {hasFormalDod ? (
              <div className="flex items-center gap-2 text-[12.5px] font-semibold text-success">
                <CheckCircle2 className="h-[17px] w-[17px]" />
                Гейт пройден BA
              </div>
            ) : isBa || isAdmin ? (
              openForm === 'formal-dod' ? (
                <InlineFormWrapper title="Подписать DoD" onClose={() => setOpenForm(null)}>
                  <div className="flex flex-col gap-3">
                    {[
                      { key: 'dod_ac_met', label: 'Все AC из ТЗ выполнены', value: dodAcMet, set: setDodAcMet },
                      { key: 'dod_before_after', label: 'Зафиксировано «было / стало»', value: dodBeforeAfter, set: setDodBeforeAfter },
                      { key: 'dod_protocol', label: 'Оформлен протокол приёмки', value: dodProtocol, set: setDodProtocol },
                      { key: 'dod_no_p0_p1', label: 'Нет открытых дефектов P0 / P1', value: dodNoP0P1, set: setDodNoP0P1 },
                    ].map((item) => (
                      <label key={item.key} className="flex items-center gap-3 cursor-pointer">
                        <input
                          type="checkbox"
                          checked={item.value}
                          onChange={(e) => item.set(e.target.checked)}
                          className="h-4 w-4 rounded border-input accent-primary"
                        />
                        <span className="text-[13px]">{item.label}</span>
                      </label>
                    ))}
                    <div>
                      <Label>Комментарий (опционально)</Label>
                      <Textarea
                        value={dodComment}
                        onChange={setDodComment}
                        placeholder="Примечания к DoD"
                      />
                    </div>
                    <PrimaryBtn
                      onClick={() => attestFormalDodMut.mutate()}
                      disabled={attestFormalDodMut.isPending}
                    >
                      <ShieldCheck className="h-[17px] w-[17px]" />
                      Подписать формальный DoD
                    </PrimaryBtn>
                  </div>
                </InlineFormWrapper>
              ) : (
                <SecondaryBtn onClick={() => setOpenForm('formal-dod')}>
                  <span className="flex items-center justify-center gap-2">
                    <ShieldCheck className="h-[16px] w-[16px]" />
                    Подписать формальный DoD
                  </span>
                </SecondaryBtn>
              )
            ) : (
              <MutedNote>Ожидает подписания BA.</MutedNote>
            )}
          </ActionCard>

          {/* Gate 2 — Business value */}
          <ActionCard highlight>
            <div className="mb-1 flex items-center gap-2">
              <Award className="h-5 w-5" />
              <div className="text-sm font-semibold">Гейт 2 · Ценностный</div>
            </div>
            <div className="mb-3 text-[11.5px] text-muted-foreground">Решает бизнес-заказчик</div>

            {hasBusinessValue ? (
              <div className="flex items-center gap-2 text-[12.5px] font-semibold text-success">
                <CheckCircle2 className="h-[17px] w-[17px]" />
                Ценность подтверждена
              </div>
            ) : isBusinessOwner ? (
              openForm === 'business-value' ? (
                <InlineFormWrapper title="Подтвердить ценность" onClose={() => setOpenForm(null)}>
                  <div className="flex flex-col gap-3">
                    <div>
                      <Label>Комментарий (опционально)</Label>
                      <Textarea
                        value={valueComment}
                        onChange={setValueComment}
                        placeholder="Комментарий к подтверждению"
                      />
                    </div>
                    <PrimaryBtn
                      onClick={() => attestBusinessValueMut.mutate()}
                      disabled={attestBusinessValueMut.isPending}
                    >
                      <ThumbsUp className="h-[17px] w-[17px]" />
                      Подтвердить ценность
                    </PrimaryBtn>
                  </div>
                </InlineFormWrapper>
              ) : (
                <PrimaryBtn onClick={() => setOpenForm('business-value')}>
                  <ThumbsUp className="h-[17px] w-[17px]" />
                  Подтвердить ценность
                </PrimaryBtn>
              )
            ) : (
              <MutedNote>Ожидает подтверждения бизнес-заказчиком.</MutedNote>
            )}
          </ActionCard>

          {/* BA controls */}
          {(isBa || isAdmin) && (
            <ActionCard>
              <div className="mb-3 text-[13px] font-semibold">Управление приёмкой</div>
              <div className="flex flex-col gap-2">
                <PrimaryBtn
                  onClick={() => closeMut.mutate()}
                  disabled={closeMut.isPending || !(hasFormalDod && hasBusinessValue)}
                >
                  <CheckCircle2 className="h-[17px] w-[17px]" />
                  Закрыть заявку
                </PrimaryBtn>
                {!(hasFormalDod && hasBusinessValue) && (
                  <p className="text-[11px] text-muted-foreground text-center">
                    Доступно после прохождения обоих гейтов
                  </p>
                )}

                {openForm === 'return-to-work' ? (
                  <InlineFormWrapper title="Вернуть в работу" onClose={() => setOpenForm(null)}>
                    <div className="flex flex-col gap-3">
                      <div>
                        <Label>Комментарий *</Label>
                        <Textarea
                          value={returnToWorkComment}
                          onChange={setReturnToWorkComment}
                          placeholder="Причина возврата"
                          required
                        />
                      </div>
                      <SecondaryBtn
                        onClick={() => {
                          if (!returnToWorkComment.trim()) return
                          returnToWorkMut.mutate()
                        }}
                        disabled={returnToWorkMut.isPending || !returnToWorkComment.trim()}
                      >
                        <span className="flex items-center justify-center gap-2">
                          <RotateCcw className="h-[16px] w-[16px]" />
                          Вернуть в работу
                        </span>
                      </SecondaryBtn>
                    </div>
                  </InlineFormWrapper>
                ) : (
                  <SecondaryBtn onClick={() => setOpenForm('return-to-work')}>
                    <span className="flex items-center justify-center gap-2">
                      <RotateCcw className="h-[16px] w-[16px]" />
                      Вернуть в работу
                    </span>
                  </SecondaryBtn>
                )}

                {openForm === 'accept-reject' ? (
                  RejectForm()
                ) : (
                  <DestructiveBtn onClick={() => setOpenForm('accept-reject')}>
                    <span className="flex items-center justify-center gap-2">
                      <XCircle className="h-[16px] w-[16px]" />
                      Отклонить
                    </span>
                  </DestructiveBtn>
                )}
              </div>
            </ActionCard>
          )}
        </div>
      )
    }

    if (status === 'closed') {
      return (
        <ActionCard>
          <div className="mb-2 flex items-center gap-2">
            <CheckCircle2 className="h-5 w-5 text-success" />
            <div className="text-sm font-semibold text-success">Заявка закрыта</div>
          </div>
          {ticket.closed_at && (
            <MutedNote>Закрыта {ruDate(ticket.closed_at)}</MutedNote>
          )}
        </ActionCard>
      )
    }

    if (status === 'rejected') {
      const rejectDecision = triageDecisions.find((d) => d.outcome === 'rejected')
      return (
        <ActionCard>
          <div className="mb-2 flex items-center gap-2">
            <XCircle className="h-5 w-5 text-destructive" />
            <div className="text-sm font-semibold text-destructive">Заявка отклонена</div>
          </div>
          {rejectDecision && (
            <div className="mt-2 flex flex-col gap-1.5">
              {rejectDecision.rejection_reason && (
                <div className="text-[12.5px]">
                  <span className="text-muted-foreground">Причина: </span>
                  <span className="font-semibold">
                    {REJECTION_REASON_LABELS[rejectDecision.rejection_reason]}
                  </span>
                </div>
              )}
              {rejectDecision.comment && (
                <div className="rounded-lg bg-destructive/5 px-3 py-2 text-[12.5px] text-destructive/80">
                  {rejectDecision.comment}
                </div>
              )}
            </div>
          )}
        </ActionCard>
      )
    }

    return null
  }

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div className="animate-vfade max-w-[1180px]">
      <button
        type="button"
        onClick={() => nav('/board')}
        className="mb-3.5 inline-flex items-center gap-[5px] text-[13px] font-medium text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="h-[18px] w-[18px]" />К доске заявок
      </button>

      <div className="grid grid-cols-1 items-start gap-[18px] lg:grid-cols-[1fr_340px]">
        {/* ── Left column ── */}
        <div className="flex flex-col gap-4">
          {/* 1. Metadata card */}
          <SectionCard>
            <div className="mb-2.5 flex flex-wrap items-center gap-2.5">
              <span className="font-mono text-xs font-semibold text-muted-foreground">
                #{ticket.id.slice(0, 8)}
              </span>
              <span
                className="rounded-full px-[11px] py-[3px] text-[11px] font-semibold text-white"
                style={{ background: statusMeta.color }}
              >
                {statusMeta.label}
              </span>
              {ticket.priority && (
                <span
                  className="inline-flex items-center gap-[5px] text-[11px] font-semibold"
                  style={{ color: pMeta.color }}
                >
                  <span
                    className="h-[7px] w-[7px] rounded-full"
                    style={{ background: pMeta.color }}
                  />
                  {pMeta.label}
                </span>
              )}
            </div>
            <h2 className="mb-3.5 text-[21px] font-bold leading-tight tracking-tight">
              {ticket.title}
            </h2>
            {ticket.description && (
              <p className="mb-3.5 text-[13.5px] leading-relaxed text-muted-foreground whitespace-pre-wrap">
                {ticket.description}
              </p>
            )}
            <div className="flex flex-wrap gap-[22px] text-[12.5px]">
              <div>
                <div className="mb-0.5 text-muted-foreground">Заявитель</div>
                <div className="font-semibold">
                  {shortSubject(ticket.author_id, user.subject)}
                </div>
              </div>
              <div>
                <div className="mb-0.5 text-muted-foreground">Отдел</div>
                <div className="font-semibold">{deptName}</div>
              </div>
              <div>
                <div className="mb-0.5 text-muted-foreground">Приоритет</div>
                <div className="font-semibold" style={{ color: pMeta.color }}>
                  {pMeta.label}
                </div>
              </div>
              <div>
                <div className="mb-0.5 text-muted-foreground">Создана</div>
                <div className="font-semibold">{ruDate(ticket.created_at)}</div>
              </div>
              {ticket.closed_at && (
                <div>
                  <div className="mb-0.5 text-muted-foreground">Закрыта</div>
                  <div className="font-semibold">{ruDate(ticket.closed_at)}</div>
                </div>
              )}
            </div>
          </SectionCard>

          {/* 2. Intake submission card */}
          <SectionCard>
            <div className="mb-4 text-[15px] font-semibold">Интейк-анкета</div>

            {!submission ? (
              <MutedNote>Интейк ещё не заполнен.</MutedNote>
            ) : (
              <>
                {/* AS-IS / TO-BE highlight */}
                <div className="mb-4 grid grid-cols-1 gap-3.5 sm:grid-cols-2">
                  <div className="rounded-2xl border border-border bg-card px-5 py-[18px]">
                    <div className="mb-2 flex items-center gap-[7px] text-[11px] font-semibold uppercase tracking-wider text-destructive">
                      <span className="h-2 w-2 rounded-full bg-destructive" />
                      AS-IS · как сейчас
                    </div>
                    <div className="text-[13px] leading-normal whitespace-pre-wrap">
                      {submission.payload['as_is'] != null && submission.payload['as_is'] !== ''
                        ? String(submission.payload['as_is'])
                        : '—'}
                    </div>
                  </div>
                  <div className="rounded-2xl border border-border bg-card px-5 py-[18px]">
                    <div className="mb-2 flex items-center gap-[7px] text-[11px] font-semibold uppercase tracking-wider text-success">
                      <span className="h-2 w-2 rounded-full bg-success" />
                      TO-BE · как должно
                    </div>
                    <div className="text-[13px] leading-normal whitespace-pre-wrap">
                      {submission.payload['to_be'] != null && submission.payload['to_be'] !== ''
                        ? String(submission.payload['to_be'])
                        : '—'}
                    </div>
                  </div>
                </div>

                {/* Remaining fields */}
                {payloadEntries.length > 0 && (
                  <dl className="flex flex-col gap-4">
                    {payloadEntries.map(({ key, label, value }) => (
                      <div key={key}>
                        <dt className="mb-0.5 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                          {label}
                        </dt>
                        <dd className="text-[13px] leading-relaxed whitespace-pre-wrap">{value}</dd>
                      </div>
                    ))}
                  </dl>
                )}

                {/* Submission version/date */}
                <div className="mt-4 border-t border-border pt-3 text-[11px] text-muted-foreground">
                  Версия {submission.version} · {ruDate(submission.created_at)}
                </div>
              </>
            )}
          </SectionCard>

          {/* 3. Traceability card */}
          <SectionCard>
            <div className="mb-1 flex items-center justify-between">
              <div className="text-[15px] font-semibold">Трассируемость требований</div>
              <div className="hidden font-mono text-[11px] text-muted-foreground sm:block">
                BG → BR → FR/NFR → AC → TC
              </div>
            </div>
            <p className="text-[12.5px] text-muted-foreground">
              Появится после оформления ТЗ (модуль требований — Phase 2).
            </p>
          </SectionCard>

          {/* 4. History card */}
          <SectionCard>
            <div className="mb-4 text-[15px] font-semibold">История статусов</div>

            {transitions.length === 0 ? (
              <MutedNote>Пока нет переходов.</MutedNote>
            ) : (
              <div className="flex flex-col">
                {transitions.map((tr, idx) => {
                  const toMeta = STATUS_META[tr.to_status]
                  const fromMeta = tr.from_status ? STATUS_META[tr.from_status] : null
                  const isLast = idx === transitions.length - 1
                  return (
                    <div key={tr.id} className="relative flex gap-3.5 pb-4">
                      {/* Timeline spine */}
                      <div className="flex w-5 flex-none flex-col items-center">
                        <span
                          className="mt-1 h-[9px] w-[9px] flex-none rounded-full"
                          style={{ background: toMeta.color }}
                        />
                        {!isLast && <div className="mt-1 w-0.5 flex-1 bg-border" />}
                      </div>

                      <div className="flex-1 pb-0.5">
                        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
                          <span className="text-[12.5px] font-semibold" style={{ color: toMeta.color }}>
                            {toMeta.label}
                          </span>
                          {fromMeta && (
                            <span className="text-[11.5px] text-muted-foreground">
                              от {fromMeta.label}
                            </span>
                          )}
                          <span className="text-[11px] text-muted-foreground">
                            · {shortSubject(tr.actor, user.subject)}
                          </span>
                          <span className="ml-auto text-[11px] text-muted-foreground">
                            {ruDateTime(tr.occurred_at)}
                          </span>
                        </div>
                        {tr.comment && (
                          <div className="mt-1 rounded-[7px] bg-muted/60 px-3 py-2 text-[12px] text-muted-foreground">
                            {tr.comment}
                          </div>
                        )}
                      </div>
                    </div>
                  )
                })}
              </div>
            )}
          </SectionCard>
        </div>

        {/* ── Right column: workflow cockpit ──
            Rendered as a function call, NOT <WorkflowCockpit /> — these helpers
            are defined inside the component and close over its state; rendering
            them as elements would remount the subtree (and drop input focus)
            on every keystroke. */}
        <div className="flex flex-col gap-4 lg:sticky lg:top-0">
          {WorkflowCockpit()}
        </div>
      </div>
    </div>
  )
}
