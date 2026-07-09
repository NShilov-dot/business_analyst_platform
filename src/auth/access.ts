import type { Me } from '../api/auth'

// Roles allowed to manage ANY task in the organization (mirror of the backend).
const MANAGE_ANY_ROLES = ['tenant_admin', 'platform_admin']

/** True if this task was created by the current user. */
export function isOwnTask(user: Me, ownerId: string): boolean {
  return user.subject === ownerId
}

/** True if the user may modify/delete the task (its owner, or a tenant/platform admin). */
export function canManageTask(user: Me, ownerId: string): boolean {
  return isOwnTask(user, ownerId) || user.roles.some((r) => MANAGE_ANY_ROLES.includes(r))
}
