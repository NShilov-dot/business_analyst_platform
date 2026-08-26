import type { Me } from '../api/auth'

// Keycloak realm roles → display label, in priority order (first match wins
// for the single header chip). Technical realm roles (default-roles-*,
// offline_access, uma_authorization) are deliberately absent.
export const ROLE_LABELS: Array<[string, string]> = [
  ['platform_admin', 'Администратор платформы'],
  ['tenant_admin', 'Администратор'],
  ['ba', 'Бизнес-аналитик'],
  ['business_owner', 'Бизнес-заказчик'],
  ['executor', 'Исполнитель'],
  ['approver', 'Согласующий'],
  ['tenant_user', 'Заявитель'],
]

/** Single highest-priority role label — for the header chip. */
export function primaryRoleLabel(roles: string[]): string {
  return ROLE_LABELS.find(([role]) => roles.includes(role))?.[1] ?? 'Заявитель'
}

/** All known role labels, in priority order — for the profile page. */
export function roleLabels(roles: string[]): string[] {
  return ROLE_LABELS.filter(([role]) => roles.includes(role)).map(([, label]) => label)
}

/** «Имя Фамилия», falling back to username, then subject. */
export function displayName(user: Me): string {
  const full = [user.given_name, user.family_name].filter(Boolean).join(' ')
  return full || user.name || user.username || user.subject
}

export function userInitials(user: Me): string {
  const parts = [user.given_name, user.family_name].filter((p): p is string => !!p)
  if (parts.length > 0) {
    return parts.map((part) => part[0]).join('').toUpperCase()
  }
  return (user.username ?? user.subject).slice(0, 2).toUpperCase()
}
