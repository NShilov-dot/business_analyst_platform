import {
  Bug,
  PenLine,
  Network,
  SlidersHorizontal,
  Sparkles,
  TrendingUp,
  type LucideIcon,
} from 'lucide-react'

// UI constants for the tickets domain. Status/priority/template keys mirror the
// backend enums (app.modules.tickets.domain.entities + intake_templates) 1:1 —
// there is deliberately NO translation layer. Mock ticket data used to live here;
// it now comes from /v1/tickets (see src/api/tickets.ts).

export type TicketStatus =
  | 'created'
  | 'triage'
  | 'spec_approval'
  | 'in_progress'
  | 'change_capture'
  | 'acceptance'
  | 'closed'
  | 'rejected'

export type TicketPriority = 'low' | 'medium' | 'high' | 'critical'

// The 7 live states in lifecycle order; «rejected» is the terminal reject branch.
export const WORKFLOW_ORDER: TicketStatus[] = [
  'created',
  'triage',
  'spec_approval',
  'in_progress',
  'change_capture',
  'acceptance',
  'closed',
]

export const STATUS_META: Record<TicketStatus, { label: string; color: string }> = {
  created: { label: 'Создан', color: '#64748B' },
  triage: { label: 'Триаж', color: '#D97706' },
  spec_approval: { label: 'ТЗ / Согласование', color: '#6366F1' },
  in_progress: { label: 'В работе', color: '#8B5CF6' },
  change_capture: { label: 'Фиксация', color: '#0D9488' },
  acceptance: { label: 'Приёмка', color: '#0891B2' },
  closed: { label: 'Закрыт', color: '#16A34A' },
  rejected: { label: 'Отклонён', color: '#DC2626' },
}

export const PRIORITY_ORDER: TicketPriority[] = ['critical', 'high', 'medium', 'low']

export const PRIORITY_META: Record<TicketPriority, { label: string; color: string }> = {
  critical: { label: 'Критичный', color: '#DC2626' },
  high: { label: 'Высокий', color: '#EA580C' },
  medium: { label: 'Средний', color: '#CA8A04' },
  low: { label: 'Низкий', color: '#64748B' },
}

// Priority is nullable on the backend (assigned at triage). Render a neutral chip
// until a value exists.
export function priorityMeta(
  p: TicketPriority | null | undefined,
): { label: string; color: string } {
  return p ? PRIORITY_META[p] : { label: 'Без приоритета', color: '#94A3B8' }
}

// Intake-submission mandatory core (§5 брифа). Keys + labels mirror
// app.modules.intake_templates MANDATORY_CORE_FIELDS, in the same order.
export const MANDATORY_CORE_FIELDS: { key: string; label: string }[] = [
  { key: 'problem', label: 'Проблема / потребность' },
  { key: 'expected_result', label: 'Ожидаемый результат' },
  { key: 'success_metric', label: 'Метрика успеха (числовая)' },
  { key: 'as_is', label: 'Текущее состояние (AS-IS)' },
  { key: 'to_be', label: 'Целевое состояние (TO-BE)' },
  { key: 'affected_systems', label: 'Затронутые системы и стейкхолдеры' },
  { key: 'urgency_deadline', label: 'Срочность и дедлайн' },
  { key: 'acceptance_criteria', label: 'Критерии приёмки (AC)' },
  { key: 'business_goal', label: 'Цель бизнеса (BG)' },
]

export const CORE_LABELS: Record<string, string> = Object.fromEntries(
  MANDATORY_CORE_FIELDS.map((f) => [f.key, f.label]),
)

// Human label for any payload key — mandatory-core keys map to their brief label,
// unknown keys are humanised (snake_case → Sentence case).
export function fieldLabel(key: string): string {
  return CORE_LABELS[key] ?? key.replace(/_/g, ' ').replace(/^\w/, (c) => c.toUpperCase())
}

// Intake template types (app.modules.intake_templates TemplateType). Only the
// free_form system template is seeded in Phase 1; the others describe intake
// types a BA can publish later.
export type TemplateType =
  | 'feature_request'
  | 'change'
  | 'defect'
  | 'integration_data'
  | 'analytics_request'
  | 'free_form'

export const TEMPLATE_TYPE_META: Record<
  TemplateType,
  { label: string; desc: string; doc: string; icon: LucideIcon }
> = {
  feature_request: {
    label: 'Запрос на фичу',
    desc: 'Новая функциональность с гипотезой ценности',
    doc: 'BRD → SRS',
    icon: Sparkles,
  },
  change: {
    label: 'Доработка / логика',
    desc: 'Изменение поведения системы',
    doc: 'Change Spec',
    icon: SlidersHorizontal,
  },
  defect: {
    label: 'Исправление / дефект',
    desc: 'Ожидаемое против фактического',
    doc: 'Change Spec (мин.)',
    icon: Bug,
  },
  integration_data: {
    label: 'Интеграция / данные',
    desc: 'Источник, обмен, выгрузки',
    doc: 'SRS + Change Spec',
    icon: Network,
  },
  analytics_request: {
    label: 'Аналитический запрос',
    desc: 'Отчёт, дашборд, исследование',
    doc: 'Change Spec',
    icon: TrendingUp,
  },
  free_form: {
    label: 'Свободная форма',
    desc: 'Нетиповой запрос + ядро метаданных',
    doc: 'Ядро + поля',
    icon: PenLine,
  },
}

export function templateTypeMeta(type: string): {
  label: string
  desc: string
  doc: string
  icon: LucideIcon
} {
  return TEMPLATE_TYPE_META[type as TemplateType] ?? TEMPLATE_TYPE_META.free_form
}
