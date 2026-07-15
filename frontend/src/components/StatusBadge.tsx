import { cva } from 'class-variance-authority'
import type { TaskStatus } from '../types/api'
import { Badge } from '@/components/ui/badge'

// Declarative status -> colour mapping via cva, rendered through the shared
// Badge primitive. Each status carries an explicit dark-mode variant so the
// chips stay legible in both colour schemes.
const statusStyles = cva(
  'rounded px-2 py-0.5 font-medium shrink-0 border-transparent',
  {
    variants: {
      status: {
        open:        'bg-blue-100 text-blue-700 hover:bg-blue-100 dark:bg-blue-950 dark:text-blue-300 dark:hover:bg-blue-950',
        in_progress: 'bg-yellow-100 text-yellow-700 hover:bg-yellow-100 dark:bg-yellow-950 dark:text-yellow-300 dark:hover:bg-yellow-950',
        done:        'bg-green-100 text-green-700 hover:bg-green-100 dark:bg-green-950 dark:text-green-300 dark:hover:bg-green-950',
        cancelled:   'bg-gray-100 text-gray-500 hover:bg-gray-100 dark:bg-gray-800 dark:text-gray-400 dark:hover:bg-gray-800',
      },
    },
  },
)

const LABELS: Record<TaskStatus, string> = {
  open:        'Open',
  in_progress: 'In Progress',
  done:        'Done',
  cancelled:   'Cancelled',
}

export function StatusBadge({ status }: { status: TaskStatus }) {
  return (
    <Badge variant="secondary" className={statusStyles({ status })}>
      {LABELS[status]}
    </Badge>
  )
}
