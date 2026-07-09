import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { tasksApi } from '../api/tasks'
import type { TaskStatus } from '../types/api'
import { TaskCard } from '../components/TaskCard'
import { LoadingSpinner } from '../components/LoadingSpinner'
import { Button } from '@/components/ui/button'
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'

const STATUS_TABS: { value: TaskStatus | ''; label: string }[] = [
  { value: '',            label: 'All' },
  { value: 'open',       label: 'Open' },
  { value: 'in_progress', label: 'In Progress' },
  { value: 'done',       label: 'Done' },
  { value: 'cancelled',  label: 'Cancelled' },
]

const PAGE_SIZE = 20

// Selected ToggleGroupItem -> solid primary so the active filter is obvious
// (the default data-[state=on]:bg-accent is too faint — --accent equals --muted).
const ACTIVE_ITEM =
  'data-[state=on]:bg-primary data-[state=on]:text-primary-foreground data-[state=on]:border-primary'

export default function TasksPage() {
  const [filter, setFilter] = useState<TaskStatus | ''>('')
  const [scope, setScope] = useState<'all' | 'mine'>('all')
  const [page, setPage] = useState(0)

  const { data, isLoading, isError } = useQuery({
    queryKey: ['tasks', filter, scope, page],
    queryFn: () =>
      tasksApi.list({
        status: filter || undefined,
        mine:   scope === 'mine',
        limit:  PAGE_SIZE,
        offset: page * PAGE_SIZE,
      }),
  })

  const tasks      = data?.data ?? []
  const total      = data?.meta.total ?? 0
  const totalPages = Math.ceil(total / PAGE_SIZE)

  function switchFilter(next: TaskStatus | '') {
    setFilter(next)
    setPage(0)
  }

  return (
    <div>
      {/* Top bar */}
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-semibold text-foreground">Tasks</h1>
        <Button asChild>
          <Link to="/tasks/new">+ New Task</Link>
        </Button>
      </div>

      {/* Scope: all org tasks vs only mine */}
      <ToggleGroup
        type="single"
        value={scope}
        onValueChange={(v) => { if (v) { setScope(v as 'all' | 'mine'); setPage(0) } }}
        variant="outline"
        size="sm"
        aria-label="Filter by scope"
        className="justify-start gap-1 mb-3"
      >
        <ToggleGroupItem value="all" className={ACTIVE_ITEM}>All (organization)</ToggleGroupItem>
        <ToggleGroupItem value="mine" className={ACTIVE_ITEM}>My tasks</ToggleGroupItem>
      </ToggleGroup>

      {/* Status filter — single-select radiogroup (not Tabs: there are no tabpanels) */}
      <ToggleGroup
        type="single"
        value={filter || 'all'}
        onValueChange={(v) => { if (v) switchFilter(v === 'all' ? '' : (v as TaskStatus)) }}
        variant="outline"
        size="sm"
        aria-label="Filter by status"
        className="justify-start flex-wrap gap-1 mb-5"
      >
        {STATUS_TABS.map(({ value, label }) => (
          <ToggleGroupItem key={value || 'all'} value={value || 'all'} className={ACTIVE_ITEM}>
            {label}
          </ToggleGroupItem>
        ))}
      </ToggleGroup>

      {isLoading && <LoadingSpinner />}

      {isError && (
        <p className="text-red-600 dark:text-red-400 text-sm py-4">Failed to load tasks. Try again.</p>
      )}

      {!isLoading && !isError && tasks.length === 0 && (
        <div className="text-center py-16 text-muted-foreground">
          <p className="text-lg mb-2">No tasks here</p>
          <Link
            to="/tasks/new"
            className="text-primary text-sm hover:underline"
          >
            Create your first task →
          </Link>
        </div>
      )}

      <div className="grid gap-3">
        {tasks.map(task => (
          <TaskCard key={task.id} task={task} />
        ))}
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex justify-center items-center gap-2 mt-6">
          <Button
            variant="outline"
            size="sm"
            disabled={page === 0}
            onClick={() => setPage(p => p - 1)}
          >
            ← Prev
          </Button>
          <span className="text-sm text-muted-foreground px-2">
            {page + 1} / {totalPages}
          </span>
          <Button
            variant="outline"
            size="sm"
            disabled={page >= totalPages - 1}
            onClick={() => setPage(p => p + 1)}
          >
            Next →
          </Button>
        </div>
      )}
    </div>
  )
}
