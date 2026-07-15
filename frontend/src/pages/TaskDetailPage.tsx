import { useNavigate, useParams, Link } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { tasksApi } from '../api/tasks'
import type { TaskStatus } from '../types/api'
import { useAuth } from '../auth/AuthProvider'
import { canManageTask, isOwnTask } from '../auth/access'
import { StatusBadge } from '../components/StatusBadge'
import { LoadingSpinner } from '../components/LoadingSpinner'
import { Button, buttonVariants } from '@/components/ui/button'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog'

const PRIORITY_LABEL = { low: 'Low', medium: 'Medium', high: 'High' } as const

export default function TaskDetailPage() {
  const { id }      = useParams<{ id: string }>()
  const navigate    = useNavigate()
  const qc          = useQueryClient()
  const { user }    = useAuth()

  const { data, isLoading } = useQuery({
    queryKey: ['tasks', id],
    queryFn:  () => tasksApi.get(id!),
    enabled:  !!id,
  })

  const invalidate = () => qc.invalidateQueries({ queryKey: ['tasks'] })

  const startM    = useMutation({ mutationFn: () => tasksApi.start(id!),    onSuccess: invalidate })
  const completeM = useMutation({ mutationFn: () => tasksApi.complete(id!), onSuccess: invalidate })
  const cancelM   = useMutation({ mutationFn: () => tasksApi.cancel(id!),   onSuccess: invalidate })
  const deleteM   = useMutation({
    mutationFn: () => tasksApi.delete(id!),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['tasks'] })
      navigate('/tasks')
    },
  })

  const busy = startM.isPending || completeM.isPending || cancelM.isPending || deleteM.isPending

  if (isLoading) return <LoadingSpinner />

  const task = data?.data
  if (!task) return <p className="text-muted-foreground py-8">Task not found.</p>

  const s = task.status as TaskStatus
  const own = isOwnTask(user, task.owner_id)
  const canManage = canManageTask(user, task.owner_id)

  return (
    <div className="max-w-lg">
      {/* Breadcrumb */}
      <div className="flex items-center gap-2 mb-6 text-sm">
        <Link to="/tasks" className="text-muted-foreground hover:text-foreground">← Tasks</Link>
      </div>

      <div className="bg-card border border-border rounded-lg p-6">
        {/* Header */}
        <div className="flex items-start justify-between gap-2 mb-4">
          <h1 className="text-xl font-semibold text-foreground leading-snug">{task.title}</h1>
          <StatusBadge status={task.status} />
        </div>

        {task.description && (
          <p className="text-muted-foreground text-sm mb-5 whitespace-pre-wrap leading-relaxed">
            {task.description}
          </p>
        )}

        {/* Meta grid */}
        <dl className="grid grid-cols-2 gap-y-3 text-sm mb-6">
          <div>
            <dt className="text-muted-foreground text-xs uppercase tracking-wide mb-0.5">Owner</dt>
            <dd className="text-foreground font-medium" title={task.owner_id}>
              {own ? 'You' : task.owner_id.slice(0, 8)}
            </dd>
          </div>

          <div>
            <dt className="text-muted-foreground text-xs uppercase tracking-wide mb-0.5">Priority</dt>
            <dd className="text-foreground font-medium">{PRIORITY_LABEL[task.priority]}</dd>
          </div>

          {task.due_at && (
            <div>
              <dt className="text-muted-foreground text-xs uppercase tracking-wide mb-0.5">Due</dt>
              <dd className="text-foreground">{new Date(task.due_at).toLocaleString()}</dd>
            </div>
          )}

          {task.completed_at && (
            <div>
              <dt className="text-muted-foreground text-xs uppercase tracking-wide mb-0.5">Completed</dt>
              <dd className="text-foreground">{new Date(task.completed_at).toLocaleString()}</dd>
            </div>
          )}

          <div>
            <dt className="text-muted-foreground text-xs uppercase tracking-wide mb-0.5">Created</dt>
            <dd className="text-foreground">{new Date(task.created_at).toLocaleDateString()}</dd>
          </div>
        </dl>

        {/* Action buttons — only the owner (or a tenant admin) can modify a task */}
        {canManage ? (
          <div className="flex flex-wrap gap-2 pt-4 border-t border-border">
            {s === 'open' && (
              <Button variant="outline" size="sm" onClick={() => startM.mutate()} disabled={busy}>
                Start
              </Button>
            )}
            {(s === 'open' || s === 'in_progress') && (
              <Button variant="outline" size="sm" onClick={() => completeM.mutate()} disabled={busy}>
                Complete
              </Button>
            )}
            {(s === 'open' || s === 'in_progress') && (
              <Button variant="outline" size="sm" onClick={() => cancelM.mutate()} disabled={busy}>
                Cancel
              </Button>
            )}
            <AlertDialog>
              <AlertDialogTrigger asChild>
                <Button variant="destructive" size="sm" disabled={busy}>
                  Delete
                </Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>Delete this task?</AlertDialogTitle>
                  <AlertDialogDescription>
                    This action cannot be undone. The task will be permanently removed.
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>Cancel</AlertDialogCancel>
                  <AlertDialogAction
                    className={buttonVariants({ variant: 'destructive' })}
                    onClick={() => deleteM.mutate()}
                  >
                    Delete
                  </AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          </div>
        ) : (
          <p className="pt-4 border-t border-border text-sm text-muted-foreground">
            Read-only — this task belongs to another member of your organization.
          </p>
        )}
      </div>
    </div>
  )
}
