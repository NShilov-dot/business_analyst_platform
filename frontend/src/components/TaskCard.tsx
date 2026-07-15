import { Link } from 'react-router-dom'
import type { Task } from '../types/api'
import { useAuth } from '../auth/AuthProvider'
import { isOwnTask } from '../auth/access'
import { StatusBadge } from './StatusBadge'
import { Card } from '@/components/ui/card'

const PRIORITY_LABEL = { low: 'Low', medium: 'Med', high: 'High' } as const
const PRIORITY_DOT   = { low: 'bg-gray-400', medium: 'bg-yellow-400', high: 'bg-red-500' } as const

export function TaskCard({ task }: { task: Task }) {
  const { user } = useAuth()
  const own = isOwnTask(user, task.owner_id)
  const due = task.due_at ? new Date(task.due_at) : null
  const overdue =
    due !== null &&
    due < new Date() &&
    task.status !== 'done' &&
    task.status !== 'cancelled'

  return (
    <Link
      to={`/tasks/${task.id}`}
      className="block rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
    >
      <Card className="p-4 shadow-none transition-colors hover:border-primary/50 hover:shadow-md">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <p className="font-medium text-foreground truncate">{task.title}</p>
            {task.description && (
              <p className="text-sm text-muted-foreground mt-0.5 line-clamp-2">{task.description}</p>
            )}
          </div>
          <StatusBadge status={task.status} />
        </div>

        <div className="flex items-center gap-3 mt-3 text-xs text-muted-foreground">
          <span className="flex items-center gap-1">
            <span className={`w-1.5 h-1.5 rounded-full ${PRIORITY_DOT[task.priority]}`} />
            {PRIORITY_LABEL[task.priority]}
          </span>
          {due && (
            <span className={overdue ? 'text-red-600 dark:text-red-400 font-medium' : ''}>
              Due {due.toLocaleDateString()}
            </span>
          )}
          <span
            className={`ml-auto px-1.5 py-0.5 rounded text-[11px] ${
              own ? 'bg-primary/10 text-primary' : 'bg-muted text-muted-foreground'
            }`}
            title={own ? 'Your task' : `Owner ${task.owner_id}`}
          >
            {own ? 'You' : task.owner_id.slice(0, 8)}
          </span>
        </div>
      </Card>
    </Link>
  )
}
