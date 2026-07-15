/**
 * UserCombobox — searchable user picker backed by /v1/directory/users.
 *
 * Controlled: value = selected Keycloak subject (UUID) | null.
 * Displays full_name in the text input; emits the subject UUID on selection.
 */

import { useRef, useState, useEffect, useCallback } from 'react'
import { useQuery } from '@tanstack/react-query'
import { X } from 'lucide-react'
import { cn } from '@/lib/utils'
import { directoryApi } from '@/api/directory'
import type { DirectoryUser } from '@/api/directory'

interface Props {
  value: string | null
  onChange: (subject: string | null) => void
  placeholder?: string
  required?: boolean
}

const MAX_VISIBLE = 8

export function UserCombobox({ value, onChange, placeholder, required }: Props) {
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState(false)
  const [highlightIdx, setHighlightIdx] = useState(-1)
  const inputRef = useRef<HTMLInputElement>(null)
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const { data, isError } = useQuery({
    queryKey: ['directory', 'users'],
    queryFn: () => directoryApi.users(undefined, 100),
    staleTime: 5 * 60 * 1000,
  })

  const users: DirectoryUser[] = data?.data ?? []

  // Derive the display name for the currently selected subject.
  const selectedUser = value ? users.find((u) => u.subject === value) ?? null : null
  const selectedLabel = selectedUser ? selectedUser.full_name : (value ?? '')

  // Keep input text in sync with the selected value when it changes externally.
  useEffect(() => {
    if (value) {
      setQuery(selectedLabel)
    } else {
      setQuery('')
    }
  }, [value, selectedLabel])

  // Filter users by the typed query (only when no value is committed).
  const filtered = value
    ? []
    : users
        .filter((u) => {
          if (!query.trim()) return true
          const q = query.toLowerCase()
          return (
            u.full_name.toLowerCase().includes(q) ||
            u.username.toLowerCase().includes(q) ||
            (u.email ?? '').toLowerCase().includes(q)
          )
        })
        .slice(0, MAX_VISIBLE)

  const showDropdown = open && !value

  function selectUser(user: DirectoryUser) {
    onChange(user.subject)
    setQuery(user.full_name)
    setOpen(false)
    setHighlightIdx(-1)
  }

  function clearSelection() {
    onChange(null)
    setQuery('')
    setHighlightIdx(-1)
    setOpen(false)
    // Re-focus the input so the user can start typing immediately.
    requestAnimationFrame(() => inputRef.current?.focus())
  }

  const handleInputChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      // If a value is already selected and the user types, clear the selection first.
      if (value) {
        onChange(null)
      }
      setQuery(e.target.value)
      setOpen(true)
      setHighlightIdx(-1)
    },
    [value, onChange],
  )

  function handleFocus() {
    if (closeTimer.current) clearTimeout(closeTimer.current)
    if (!value) setOpen(true)
  }

  function handleBlur() {
    // Delay so a click on an option registers before the dropdown closes.
    closeTimer.current = setTimeout(() => setOpen(false), 150)
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (!showDropdown) return
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setHighlightIdx((i) => Math.min(i + 1, filtered.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setHighlightIdx((i) => Math.max(i - 1, 0))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      if (highlightIdx >= 0 && filtered[highlightIdx]) {
        selectUser(filtered[highlightIdx])
      }
    } else if (e.key === 'Escape') {
      setOpen(false)
      setHighlightIdx(-1)
    }
  }

  return (
    <div className="relative w-full">
      <div className="relative">
        <input
          ref={inputRef}
          type="text"
          value={query}
          onChange={handleInputChange}
          onFocus={handleFocus}
          onBlur={handleBlur}
          onKeyDown={handleKeyDown}
          placeholder={value ? undefined : placeholder}
          required={required && !value}
          autoComplete="off"
          className={cn(
            'w-full rounded-lg border border-input bg-background py-2 text-[13px]',
            'focus:outline-none focus-visible:ring-2 focus-visible:ring-ring',
            value ? 'pr-8 pl-3 font-medium' : 'px-3',
          )}
        />
        {value && (
          <button
            type="button"
            onClick={clearSelection}
            className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
            tabIndex={-1}
            aria-label="Очистить выбор"
          >
            <X className="h-[14px] w-[14px]" />
          </button>
        )}
      </div>

      {showDropdown && (
        <div
          className={cn(
            'absolute left-0 right-0 z-50 mt-1',
            'max-h-[272px] overflow-y-auto',
            'rounded-lg border border-border bg-card shadow-md',
          )}
          // Prevent mousedown from triggering blur on the input before the click fires.
          onMouseDown={(e) => e.preventDefault()}
        >
          {isError ? (
            <div className="px-3 py-2 text-[12.5px] text-muted-foreground">
              Ошибка загрузки директории
            </div>
          ) : users.length === 0 ? (
            <div className="px-3 py-2 text-[12.5px] text-muted-foreground">
              Директория недоступна
            </div>
          ) : filtered.length === 0 ? (
            <div className="px-3 py-2 text-[12.5px] text-muted-foreground">
              Пользователи не найдены
            </div>
          ) : (
            filtered.map((user, idx) => (
              <button
                key={user.subject}
                type="button"
                onClick={() => selectUser(user)}
                className={cn(
                  'flex w-full flex-col items-start px-3 py-2 text-left',
                  'border-b border-border last:border-b-0',
                  idx === highlightIdx
                    ? 'bg-primary/10 text-foreground'
                    : 'hover:bg-muted/60 text-foreground',
                )}
              >
                <span className="text-[13px] font-semibold leading-snug">{user.full_name}</span>
                <span className="text-[11.5px] text-muted-foreground">
                  @{user.username}
                  {user.email ? ` · ${user.email}` : ''}
                </span>
              </button>
            ))
          )}
        </div>
      )}
    </div>
  )
}
