/**
 * DocumentAttach — a paperclip control for attaching documents to a new
 * AI-intake dialog. Opens a popover with upload + a searchable list of the
 * tenant's ready documents (instead of dumping the whole library inline).
 * Only "extracted" documents can be attached. Selected docs show as chips.
 */

import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, FileText, Loader2, Paperclip, Search, Upload, X } from 'lucide-react'
import { toast } from 'sonner'
import { cn } from '@/lib/utils'
import { getErrorMessage } from '@/lib/errors'
import { documentsApi } from '@/api/documents'

interface Props {
  selectedIds: string[]
  onChange: (ids: string[]) => void
  disabled?: boolean
}

export function DocumentAttach({ selectedIds, onChange, disabled }: Props) {
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const boxRef = useRef<HTMLDivElement>(null)

  const { data } = useQuery({ queryKey: ['documents'], queryFn: () => documentsApi.list() })
  const docs = data?.data ?? []
  const selectedDocs = docs.filter((d) => selectedIds.includes(d.id))

  const upload = useMutation({
    mutationFn: (file: File) => documentsApi.upload(file),
    onSuccess: ({ data: doc }) => {
      qc.invalidateQueries({ queryKey: ['documents'] })
      if (doc.status === 'extracted') onChange([...selectedIds, doc.id])
      else if (doc.status === 'failed') toast.error('Не удалось извлечь текст из документа')
      else toast.success('Документ загружен')
    },
    onError: (err) => toast.error(getErrorMessage(err, 'Не удалось загрузить документ')),
  })

  // Close the popover on an outside click.
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [open])

  const ready = docs.filter((d) => d.status === 'extracted')
  const filtered = query.trim()
    ? ready.filter((d) => d.filename.toLowerCase().includes(query.toLowerCase()))
    : ready

  const toggle = (id: string) =>
    onChange(selectedIds.includes(id) ? selectedIds.filter((x) => x !== id) : [...selectedIds, id])

  return (
    <div className="flex flex-wrap items-center gap-2">
      <div ref={boxRef} className="relative">
        <button
          type="button"
          disabled={disabled}
          onClick={() => setOpen((o) => !o)}
          aria-label="Приложить документ"
          aria-expanded={open}
          className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-input bg-card px-3 text-[12.5px] font-semibold hover:bg-muted disabled:opacity-50"
        >
          <Paperclip className="h-4 w-4" />
          Приложить
        </button>

        {open && (
          <div className="absolute bottom-full left-0 z-50 mb-2 w-[300px] rounded-xl border border-border bg-card p-2 shadow-lg">
            <label
              className={cn(
                'mb-2 flex cursor-pointer items-center gap-2 rounded-lg border border-dashed border-input px-3 py-2 text-[12.5px] font-semibold hover:bg-muted',
                upload.isPending && 'pointer-events-none opacity-60',
              )}
            >
              {upload.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Upload className="h-4 w-4" />
              )}
              Загрузить файл
              <input
                type="file"
                accept=".pdf,.docx,.txt,.md"
                className="hidden"
                disabled={upload.isPending}
                onChange={(e) => {
                  const file = e.target.files?.[0]
                  e.target.value = ''
                  if (file) upload.mutate(file)
                }}
              />
            </label>

            <div className="relative mb-1.5">
              <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Поиск по документам…"
                className="w-full rounded-lg border border-input bg-background py-1.5 pl-8 pr-2 text-[12.5px] focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              />
            </div>

            <div className="max-h-[200px] overflow-y-auto">
              {filtered.length === 0 ? (
                <div className="px-2 py-4 text-center text-[12px] text-muted-foreground">
                  {ready.length === 0 ? 'Готовых документов пока нет' : 'Ничего не найдено'}
                </div>
              ) : (
                filtered.map((d) => {
                  const on = selectedIds.includes(d.id)
                  return (
                    <button
                      key={d.id}
                      type="button"
                      onClick={() => toggle(d.id)}
                      className={cn(
                        'flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-[12.5px]',
                        on ? 'bg-primary/10' : 'hover:bg-muted',
                      )}
                    >
                      <span
                        className={cn(
                          'flex h-4 w-4 flex-none items-center justify-center rounded border',
                          on ? 'border-primary bg-primary text-primary-foreground' : 'border-input',
                        )}
                      >
                        {on && <Check className="h-3 w-3" />}
                      </span>
                      <FileText className="h-3.5 w-3.5 flex-none text-muted-foreground" />
                      <span className="truncate">{d.filename}</span>
                    </button>
                  )
                })
              )}
            </div>
          </div>
        )}
      </div>

      {selectedDocs.map((d) => (
        <span
          key={d.id}
          className="inline-flex max-w-[220px] items-center gap-1.5 rounded-full border border-primary bg-primary/10 px-2.5 py-1 text-[11.5px] font-medium"
          title={d.filename}
        >
          <FileText className="h-3 w-3 flex-none" />
          <span className="truncate">{d.filename}</span>
          <button
            type="button"
            onClick={() => toggle(d.id)}
            aria-label={`Убрать ${d.filename}`}
            className="flex-none text-muted-foreground hover:text-foreground"
          >
            <X className="h-3 w-3" />
          </button>
        </span>
      ))}
    </div>
  )
}
