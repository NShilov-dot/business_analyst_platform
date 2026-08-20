import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { AlertCircle, FileText, Loader2, Trash2, Upload } from 'lucide-react'
import { toast } from 'sonner'
import { cn } from '@/lib/utils'
import { getErrorMessage } from '@/lib/errors'
import { documentsApi, type DocumentStatus } from '@/api/documents'
import { LoadingSpinner } from '@/components/LoadingSpinner'

// Библиотека документов тенанта: загрузка сюда делает файл доступным для
// приложения к диалогу ИИ-ассистента на /intake.

const STATUS_LABEL: Record<DocumentStatus, string> = {
  uploaded: 'Обработка',
  extracted: 'Готов',
  failed: 'Ошибка',
}
const STATUS_CLASSES: Record<DocumentStatus, string> = {
  uploaded: 'bg-muted text-muted-foreground',
  extracted: 'bg-success/15 text-success',
  failed: 'bg-destructive/15 text-destructive',
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} Б`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} КБ`
  return `${(bytes / (1024 * 1024)).toFixed(1)} МБ`
}

export default function DocumentsPage() {
  const qc = useQueryClient()

  const documentsQ = useQuery({
    queryKey: ['documents'],
    queryFn: () => documentsApi.list(),
  })
  const documents = documentsQ.data?.data ?? []

  const uploadMutation = useMutation({
    mutationFn: (file: File) => documentsApi.upload(file),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['documents'] })
      toast.success('Документ загружен')
    },
    onError: (err) => toast.error(getErrorMessage(err, 'Не удалось загрузить документ')),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => documentsApi.remove(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['documents'] })
      toast.success('Документ удалён')
    },
    onError: (err) => toast.error(getErrorMessage(err, 'Не удалось удалить документ')),
  })

  return (
    <div className="animate-vfade mx-auto max-w-[900px]">
      <div className="mb-5 flex items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-bold">Документы</h1>
          <p className="mt-0.5 text-[12.5px] text-muted-foreground">
            Загрузите материалы, чтобы приложить их к диалогу с ИИ-ассистентом на «Новой заявке».
          </p>
        </div>
        <label
          className={cn(
            'flex flex-none cursor-pointer items-center gap-2 rounded-xl bg-primary px-[16px] py-[10px] text-[13px] font-bold text-primary-foreground hover:bg-primary/90',
            uploadMutation.isPending && 'pointer-events-none opacity-60',
          )}
        >
          {uploadMutation.isPending ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Upload className="h-4 w-4" />
          )}
          Загрузить
          <input
            type="file"
            accept=".pdf,.docx,.txt,.md"
            className="hidden"
            disabled={uploadMutation.isPending}
            onChange={(e) => {
              const file = e.target.files?.[0]
              e.target.value = ''
              if (file) uploadMutation.mutate(file)
            }}
          />
        </label>
      </div>

      {documentsQ.isLoading && <LoadingSpinner label="Загрузка документов…" />}

      {documentsQ.isError && (
        <div className="flex items-center gap-2 rounded-[10px] bg-destructive/10 px-3.5 py-[11px] text-[12.5px] font-medium text-destructive">
          <AlertCircle className="h-[18px] w-[18px] flex-none" />
          Не удалось загрузить список документов.
        </div>
      )}

      {!documentsQ.isLoading && !documentsQ.isError && documents.length === 0 && (
        <div className="rounded-2xl border border-border bg-card p-8 text-center text-[13px] text-muted-foreground">
          Документов пока нет — загрузите первый файл.
        </div>
      )}

      {documents.length > 0 && (
        <div className="overflow-hidden rounded-2xl border border-border bg-card">
          <table className="w-full text-left text-[12.5px]">
            <thead>
              <tr className="border-b border-border text-[11px] uppercase tracking-wider text-muted-foreground">
                <th className="px-4 py-3 font-semibold">Файл</th>
                <th className="px-4 py-3 font-semibold">Размер</th>
                <th className="px-4 py-3 font-semibold">Статус</th>
                <th className="px-4 py-3 font-semibold">Загружен</th>
                <th className="px-4 py-3" />
              </tr>
            </thead>
            <tbody>
              {documents.map((doc) => (
                <tr key={doc.id} className="border-b border-border last:border-0">
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2.5">
                      <FileText className="h-4 w-4 flex-none text-muted-foreground" />
                      <div className="min-w-0">
                        <div className="truncate font-medium">{doc.filename}</div>
                        {doc.status === 'failed' && doc.extraction_error && (
                          <div className="mt-0.5 truncate text-[11px] text-destructive">
                            {doc.extraction_error}
                          </div>
                        )}
                      </div>
                    </div>
                  </td>
                  <td className="px-4 py-3 text-muted-foreground">{formatSize(doc.size_bytes)}</td>
                  <td className="px-4 py-3">
                    <span
                      className={cn(
                        'rounded-full px-2.5 py-0.5 text-[11px] font-semibold',
                        STATUS_CLASSES[doc.status],
                      )}
                    >
                      {STATUS_LABEL[doc.status]}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-muted-foreground">
                    {new Date(doc.created_at).toLocaleString('ru-RU')}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <button
                      type="button"
                      onClick={() => deleteMutation.mutate(doc.id)}
                      disabled={deleteMutation.isPending}
                      aria-label={`Удалить ${doc.filename}`}
                      className="rounded-lg p-1.5 text-muted-foreground hover:bg-destructive/10 hover:text-destructive disabled:opacity-40"
                    >
                      <Trash2 className="h-4 w-4" />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
