import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Bot, ChevronRight, FileText, Loader2, Upload } from 'lucide-react'
import { toast } from 'sonner'
import { cn } from '@/lib/utils'
import { getErrorMessage } from '@/lib/errors'
import { documentsApi, type DocumentStatus } from '@/api/documents'
import { intakeChatApi } from '@/api/intakeChat'
import { LoadingSpinner } from '@/components/LoadingSpinner'

// Russian status badge for an attached document (mirrors DocumentsPage.tsx).
const DOC_STATUS_LABEL: Record<DocumentStatus, string> = {
  uploaded: 'Обработка',
  extracted: 'Готов',
  failed: 'Ошибка',
}
const DOC_STATUS_CLASSES: Record<DocumentStatus, string> = {
  uploaded: 'bg-muted text-muted-foreground',
  extracted: 'bg-success/15 text-success',
  failed: 'bg-destructive/15 text-destructive',
}

// Entry point for a new request: AI-assisted intake only. Attach optional
// documents, start a chat session, and hand off to /intake/chat.
export const INTAKE_SESSION_STORAGE_KEY = 'intake:sessionId'

export default function IntakePage() {
  const navigate = useNavigate()
  const qc = useQueryClient()

  const [selectedDocIds, setSelectedDocIds] = useState<string[]>([])

  // Documents that can be attached to the AI-intake dialog
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

  const startMutation = useMutation({
    mutationFn: () => intakeChatApi.startSession(undefined, selectedDocIds),
    onSuccess: ({ data }) => {
      sessionStorage.setItem(INTAKE_SESSION_STORAGE_KEY, data.session.id)
      navigate('/intake/chat')
    },
    onError: (err) => toast.error(getErrorMessage(err, 'Не удалось начать диалог')),
  })

  const toggleDoc = (id: string) =>
    setSelectedDocIds((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id],
    )

  const hasUnreadySelected = selectedDocIds.some(
    (id) => documents.find((d) => d.id === id)?.status !== 'extracted',
  )

  return (
    <div className="animate-vfade mx-auto max-w-[620px]">
      <div className="rounded-2xl border-[1.5px] border-primary bg-card p-4 shadow-[0_4px_14px_rgba(0,0,0,.06)] sm:p-5">
        <div className="flex items-center gap-3.5">
          <div className="flex h-11 w-11 flex-none items-center justify-center rounded-xl bg-primary text-primary-foreground">
            <Bot className="h-6 w-6" />
          </div>
          <div className="min-w-0 flex-1">
            <div className="text-[13.5px] font-semibold">Собрать заявку с ИИ-ассистентом</div>
            <div className="mt-0.5 text-[12px] text-muted-foreground">
              Опишите проблему своими словами — ассистент задаст вопросы и заполнит шаблон за вас.
              Можно приложить документы — ассистент изучит их перед началом диалога.
            </div>
          </div>
        </div>

        <div className="mt-4 border-t border-border pt-4">
          <label
            className={cn(
              'mb-3 flex w-fit cursor-pointer items-center gap-2 rounded-lg border border-input bg-card px-3 py-2 text-[12.5px] font-semibold hover:bg-muted',
              uploadMutation.isPending && 'pointer-events-none opacity-60',
            )}
          >
            {uploadMutation.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Upload className="h-4 w-4" />
            )}
            Приложить документ
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

          {documentsQ.isLoading ? (
            <LoadingSpinner label="Загрузка документов…" />
          ) : (
            documents.length > 0 && (
              <ul className="mb-3 flex flex-col gap-1">
                {documents.map((doc) => (
                  <li key={doc.id}>
                    <label className="flex cursor-pointer items-center gap-2.5 rounded-lg px-2 py-1.5 hover:bg-muted">
                      <input
                        type="checkbox"
                        checked={selectedDocIds.includes(doc.id)}
                        onChange={() => toggleDoc(doc.id)}
                        className="h-4 w-4 flex-none rounded border-input"
                      />
                      <FileText className="h-4 w-4 flex-none text-muted-foreground" />
                      <span className="min-w-0 flex-1 truncate text-[12.5px]">{doc.filename}</span>
                      <span
                        className={cn(
                          'flex-none rounded-full px-2 py-0.5 text-[10.5px] font-semibold',
                          DOC_STATUS_CLASSES[doc.status],
                        )}
                      >
                        {DOC_STATUS_LABEL[doc.status]}
                      </span>
                    </label>
                  </li>
                ))}
              </ul>
            )
          )}

          {hasUnreadySelected && (
            <div className="mb-3 text-[11.5px] text-muted-foreground">
              В диалог можно передать только обработанные документы (статус «Готов»).
            </div>
          )}

          <button
            type="button"
            onClick={() => startMutation.mutate()}
            disabled={hasUnreadySelected || startMutation.isPending}
            className="flex items-center gap-1.5 rounded-xl bg-primary px-[18px] py-[10px] text-[13px] font-bold text-primary-foreground hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {startMutation.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <>
                Начать
                <ChevronRight className="h-4 w-4" />
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  )
}
