// Shared display formatters for the ticket surface (board, dashboard, detail).

export function ruDate(iso: string): string {
  return new Date(iso).toLocaleDateString('ru-RU', { day: '2-digit', month: 'short' })
}

export function ruDateTime(iso: string): string {
  return new Date(iso).toLocaleString('ru-RU', {
    day: '2-digit',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  })
}

// Resolve a Keycloak subject to a short label: 'Вы' for the current user, the
// raw id for system actors, else a short '#'-prefixed id.
export function shortSubject(subject: string, mySubject: string): string {
  if (subject === mySubject) return 'Вы'
  if (subject.startsWith('system:')) return subject
  return '#' + subject.slice(0, 8)
}
