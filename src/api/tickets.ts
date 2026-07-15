/**
 * Tickets API (/v1/tickets) — the product's core aggregate.
 *
 * Mirrors app.modules.tickets.interface.schemas. The gated, branching workflow
 * state machine lives on the backend; this client only exposes each edge as a
 * method. RBAC + gate enforcement are authoritative server-side — the UI gates
 * buttons for UX only.
 */

import { api } from './client'
import type { TicketPriority, TicketStatus } from '../features/tickets/model'

export interface Envelope<T> {
  data: T
}

export interface PagedEnvelope<T> {
  data: T[]
  meta: { total: number; limit: number; offset: number }
}

export interface Ticket {
  id: string
  title: string
  description: string | null
  status: TicketStatus
  author_id: string
  department_id: string | null
  priority: TicketPriority | null
  acceptance_cycle: number
  created_at: string
  updated_at: string
  closed_at: string | null
}

export interface Submission {
  id: string
  ticket_id: string
  version: number
  template_version_id: string
  payload: Record<string, unknown>
  created_by: string
  created_at: string
}

export interface Transition {
  id: string
  ticket_id: string
  from_status: TicketStatus | null
  to_status: TicketStatus
  actor: string
  comment: string | null
  acceptance_cycle: number
  occurred_at: string
}

export type AttestationKind = 'spec_approved' | 'formal_dod' | 'business_value'

export interface Attestation {
  id: string
  ticket_id: string
  kind: AttestationKind
  acceptance_cycle: number
  attested_by: string
  roles_snapshot: string[]
  checklist: Record<string, boolean> | null
  spec_ref: string | null
  agreed_with_subject: string | null
  comment: string | null
  attested_at: string
}

export type AssignmentRole = 'business_owner' | 'executor'

export interface Assignment {
  id: string
  ticket_id: string
  role: AssignmentRole
  subject: string
  assigned_by: string
  assigned_at: string
  unassigned_at: string | null
}

export type TriageOutcome = 'accepted' | 'returned' | 'rejected'
export type RejectionReason = 'duplicate' | 'irrelevant' | 'unjustified'

export interface TriageDecision {
  id: string
  ticket_id: string
  outcome: TriageOutcome
  department_id: string | null
  rejection_reason: RejectionReason | null
  duplicate_of_ticket_id: string | null
  comment: string | null
  decided_by: string
  decided_at: string
}

export interface TicketDetail {
  ticket: Ticket
  current_submission: Submission | null
  assignments: Assignment[]
  attestations: Attestation[]
  triage_decisions: TriageDecision[]
}

export interface ListTicketsParams {
  status?: TicketStatus
  department_id?: string
  mine?: boolean
  assigned_to_me?: boolean
  limit?: number
  offset?: number
}

export interface CreateTicketInput {
  title: string
  description?: string
  priority?: TicketPriority
  template_version_id?: string
  payload?: Record<string, unknown>
}

export interface TriageAcceptInput {
  department_id: string
  business_owner_subject: string
  executor_subject?: string
  priority?: TicketPriority
  comment?: string
}

export interface RejectInput {
  comment: string
  rejection_reason?: RejectionReason
  duplicate_of_ticket_id?: string
}

export interface SpecApprovalInput {
  // Optional: the ticket itself is the ТЗ — the backend self-references it when omitted.
  spec_ref?: string
  agreed_with_subject: string
  comment?: string
}

// The built-in free_form system template's published version (seeded per tenant).
export const FREE_FORM_VERSION_ID = '20000000-0000-0000-0000-000000000001'

export const ticketsApi = {
  list(params: ListTicketsParams = {}) {
    const qs = new URLSearchParams()
    if (params.status) qs.set('status', params.status)
    if (params.department_id) qs.set('department_id', params.department_id)
    if (params.mine) qs.set('mine', 'true')
    if (params.assigned_to_me) qs.set('assigned_to_me', 'true')
    if (params.limit !== undefined) qs.set('limit', String(params.limit))
    if (params.offset !== undefined) qs.set('offset', String(params.offset))
    const q = qs.toString() ? `?${qs}` : ''
    return api.get<PagedEnvelope<Ticket>>(`/v1/tickets${q}`)
  },

  get: (id: string) => api.get<Envelope<TicketDetail>>(`/v1/tickets/${id}`),
  transitions: (id: string) => api.get<Envelope<Transition[]>>(`/v1/tickets/${id}/transitions`),
  attestations: (id: string) => api.get<Envelope<Attestation[]>>(`/v1/tickets/${id}/attestations`),

  create: (input: CreateTicketInput) => api.post<Envelope<TicketDetail>>('/v1/tickets', input),
  submit: (id: string) => api.post<Envelope<Ticket>>(`/v1/tickets/${id}/submit`),
  triageAccept: (id: string, body: TriageAcceptInput) =>
    api.post<Envelope<Ticket>>(`/v1/tickets/${id}/triage-accept`, body),
  returnForRefinement: (id: string, comment: string) =>
    api.post<Envelope<Ticket>>(`/v1/tickets/${id}/return-for-refinement`, { comment }),
  reject: (id: string, body: RejectInput) =>
    api.post<Envelope<Ticket>>(`/v1/tickets/${id}/reject`, body),
  assign: (id: string, role: AssignmentRole, subject: string) =>
    api.post<Envelope<Assignment>>(`/v1/tickets/${id}/assign`, { role, subject }),
  attestSpecApproval: (id: string, body: SpecApprovalInput) =>
    api.post<Envelope<Attestation>>(`/v1/tickets/${id}/attest/spec-approval`, body),
  startWork: (id: string) => api.post<Envelope<Ticket>>(`/v1/tickets/${id}/start-work`),
  finishWork: (id: string) => api.post<Envelope<Ticket>>(`/v1/tickets/${id}/finish-work`),
  requestAcceptance: (id: string, changes_summary: string) =>
    api.post<Envelope<Ticket>>(`/v1/tickets/${id}/request-acceptance`, { changes_summary }),
  attestFormalDod: (id: string, checklist: Record<string, boolean>, comment?: string) =>
    api.post<Envelope<Attestation>>(`/v1/tickets/${id}/attest/formal-dod`, { checklist, comment }),
  attestBusinessValue: (id: string, comment?: string) =>
    api.post<Envelope<Attestation>>(`/v1/tickets/${id}/attest/business-value`, { comment }),
  returnToWork: (id: string, comment: string) =>
    api.post<Envelope<Ticket>>(`/v1/tickets/${id}/return-to-work`, { comment }),
  close: (id: string) => api.post<Envelope<Ticket>>(`/v1/tickets/${id}/close`),
}
