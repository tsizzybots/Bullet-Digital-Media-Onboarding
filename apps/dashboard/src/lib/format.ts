// Presentation helpers for the clients board (S1-31). Pure + framework-free so
// they are trivially unit-testable and reused by the detail page (S1-32).

import type { BadgeProps } from '@/components/ui/badge'

type BadgeVariant = NonNullable<BadgeProps['variant']>

/** Human labels for the `current_step` enum (db/enums.py CURRENT_STEP_*). */
export const STEP_LABELS: Record<string, string> = {
  sales_call: 'Sales call',
  agreement: 'Agreement',
  signed: 'Signed',
  portal: 'Portal',
  kickoff: 'Kick-off',
  build: 'Build',
  live: 'Live',
}

export function stepLabel(step: string): string {
  return STEP_LABELS[step] ?? step
}

/**
 * Compact "time in step" from an ISO timestamp, relative to now: `just now`,
 * `12m`, `3h`, `5d`. Recomputed on each render (i.e. each poll) from
 * `step_entered_at` - it does not self-tick between polls. Guards against clock
 * skew (future timestamps -> `just now`).
 */
export function formatTimeInStep(iso: string, now: Date = new Date()): string {
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return '-'
  const seconds = Math.floor((now.getTime() - then) / 1000)
  if (seconds < 60) return 'just now'
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h`
  const days = Math.floor(hours / 24)
  return `${days}d`
}

/** Map a `platform_actions.status` to a Badge variant (the health colour). */
export function actionStatusVariant(status: string | null): BadgeVariant {
  switch (status) {
    case 'success':
      return 'success'
    case 'failed':
    case 'dead_lettered':
      return 'danger'
    case 'in_progress':
    case 'pending':
      return 'warning'
    default:
      return 'neutral'
  }
}

// Tokens that are acronyms/brands, not words - title-casing "ghl" to "Ghl"
// reads wrong on the board.
const TOKEN_LABELS: Record<string, string> = {
  ghl: 'GHL',
  hubspot: 'HubSpot',
  pandadoc: 'PandaDoc',
  gsheets: 'Google Sheets',
  gdocs: 'Google Docs',
  gdrive: 'Google Drive',
  gmail: 'Gmail',
  gcal: 'Google Calendar',
}

/** Title-case a status / action token for display: `dead_lettered` -> `Dead lettered`. */
export function humanizeToken(token: string | null): string {
  if (!token) return '-'
  const known = TOKEN_LABELS[token]
  if (known) return known
  const spaced = token.replace(/_/g, ' ')
  return spaced.charAt(0).toUpperCase() + spaced.slice(1)
}

/**
 * Format an ISO timestamp as `10 Jun 2026, 14:00` (en-GB, 24-hour). Used for
 * the sales-call meeting time on the unlinked-transcripts list (S1-27a).
 * Returns `Unknown` for null / unparseable input (a transcript may have no
 * captured meeting time).
 */
export function formatDateTime(iso: string | null): string {
  if (!iso) return 'Unknown'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return 'Unknown'
  return new Intl.DateTimeFormat('en-GB', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(date)
}

/**
 * Compact transcript size: `840 chars`, `12.3k chars`. `null` -> `-` (the
 * transcript text was not captured yet). Helps the team recognise a call on
 * the unlinked list (S1-27a).
 */
export function formatCharCount(chars: number | null): string {
  if (chars == null || !Number.isFinite(chars)) return '-'
  if (chars < 1000) return `${chars} chars`
  return `${(chars / 1000).toFixed(1)}k chars`
}
