// Display metadata for ticket lifecycle audit actions — shared by the dashboard
// activity feed and the notifications bell (both render audit_entries rows).

import {
  BadgeCheck,
  Circle,
  FileEdit,
  FilePlus,
  FileX,
  GitMerge,
  ListChecks,
  PlayCircle,
  Send,
  ShieldCheck,
  Shuffle,
  ThumbsUp,
  Undo2,
  UserCog,
  XCircle,
  type LucideIcon,
} from 'lucide-react'

export interface ActionMeta {
  label: string
  icon: LucideIcon
  color: string
  soft: string
}

const ACTION_META: Record<string, ActionMeta> = {
  created: {
    label: 'создана',
    icon: FilePlus,
    color: '#64748B',
    soft: 'bg-slate-100 dark:bg-slate-800',
  },
  updated: {
    label: 'обновлена',
    icon: FileEdit,
    color: '#6366F1',
    soft: 'bg-indigo-50 dark:bg-indigo-950',
  },
  submission_replaced: {
    label: 'интейк обновлён',
    icon: FileEdit,
    color: '#8B5CF6',
    soft: 'bg-violet-50 dark:bg-violet-950',
  },
  submitted: {
    label: 'отправлена на триаж',
    icon: Send,
    color: '#D97706',
    soft: 'bg-amber-50 dark:bg-amber-950',
  },
  triage_accepted: {
    label: 'принята (ТЗ)',
    icon: ShieldCheck,
    color: '#0891B2',
    soft: 'bg-cyan-50 dark:bg-cyan-950',
  },
  returned_for_refinement: {
    label: 'возвращена на доработку',
    icon: Undo2,
    color: '#D97706',
    soft: 'bg-amber-50 dark:bg-amber-950',
  },
  rejected: {
    label: 'отклонена',
    icon: XCircle,
    color: '#DC2626',
    soft: 'bg-red-50 dark:bg-red-950',
  },
  assignment_changed: {
    label: 'назначение изменено',
    icon: UserCog,
    color: '#64748B',
    soft: 'bg-slate-100 dark:bg-slate-800',
  },
  spec_approval_attested: {
    label: 'ТЗ подписано',
    icon: ShieldCheck,
    color: '#6366F1',
    soft: 'bg-indigo-50 dark:bg-indigo-950',
  },
  work_started: {
    label: 'взята в работу',
    icon: PlayCircle,
    color: '#8B5CF6',
    soft: 'bg-violet-50 dark:bg-violet-950',
  },
  work_finished: {
    label: 'работа завершена',
    icon: BadgeCheck,
    color: '#0D9488',
    soft: 'bg-teal-50 dark:bg-teal-950',
  },
  acceptance_requested: {
    label: 'запрошена приёмка',
    icon: GitMerge,
    color: '#0891B2',
    soft: 'bg-cyan-50 dark:bg-cyan-950',
  },
  formal_dod_attested: {
    label: 'DoD подписан',
    icon: ListChecks,
    color: '#16A34A',
    soft: 'bg-green-50 dark:bg-green-950',
  },
  business_value_attested: {
    label: 'ценность подтверждена',
    icon: ThumbsUp,
    color: '#16A34A',
    soft: 'bg-green-50 dark:bg-green-950',
  },
  returned_to_work: {
    label: 'возвращена в работу',
    icon: Shuffle,
    color: '#D97706',
    soft: 'bg-amber-50 dark:bg-amber-950',
  },
  closed: {
    label: 'закрыта',
    icon: FileX,
    color: '#16A34A',
    soft: 'bg-green-50 dark:bg-green-950',
  },
}

export function actionMeta(action: string): ActionMeta {
  return (
    ACTION_META[action] ?? {
      label: action,
      icon: Circle,
      color: '#94A3B8',
      soft: 'bg-slate-100 dark:bg-slate-800',
    }
  )
}
