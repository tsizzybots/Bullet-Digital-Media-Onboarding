'use client'

import type { components } from '@bullet/shared'
import { useQuery } from '@tanstack/react-query'
import Link from 'next/link'
import type { ReactNode } from 'react'

import { Alert } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { api } from '@/lib/api'
import {
  actionStatusVariant,
  formatTimeInStep,
  humanizeToken,
  stepLabel,
} from '@/lib/format'
import { inngestRunUrl } from '@/lib/inngest'
import { PLATFORM_LINKS } from '@/lib/platform-links'

type ClientDetail = components['schemas']['ClientDetailResponse']
type KnowledgeEntry = components['schemas']['KnowledgeEntry']

// 5s polling on active fields per the S1-32 card / PRD §8.
const DETAIL_POLL_INTERVAL_MS = 5_000

// PRD §7.1 keys get tailored rendering below; anything else falls through to
// the generic renderer so schema additions never break this page.
const SUMMARY_KEY_ORDER = [
  'business_type',
  'business_goals',
  'budget_range_usd',
  'pain_points',
  'red_flags',
  'next_steps',
  'notable_quotes',
]

const isNotFound = (err: unknown): boolean =>
  err instanceof Error && err.message === 'not_found'

export function ClientDetail({ id }: { id: string }) {
  const { data, isPending, error } = useQuery({
    queryKey: ['client', id],
    queryFn: async () => {
      // Fail fast on a slow/stuck request rather than holding it open under the
      // 5s poll (pairs with the DB-side statement_timeout), matching the
      // clients-list query's 8s ceiling.
      const { data, error, response } = await api.GET('/clients/{client_id}', {
        params: { path: { client_id: id } },
        signal: AbortSignal.timeout(8_000),
      })
      if (response.status === 404) throw new Error('not_found')
      if (error || !data) throw new Error('client detail request failed')
      return data
    },
    // Function form so a not-found client STOPS polling - 404 is terminal,
    // and the plain-number form would keep refetching every 5s forever even
    // in error state (TanStack v5 intervals are independent of `retry`).
    refetchInterval: (query) =>
      isNotFound(query.state.error) ? false : DETAIL_POLL_INTERVAL_MS,
    retry: (failureCount, err) => (isNotFound(err) ? false : failureCount < 1),
  })

  if (isNotFound(error)) {
    return (
      <div className="space-y-4">
        <BackLink />
        <Alert variant="info">Client not found - it may have been removed.</Alert>
      </div>
    )
  }

  if (isPending) {
    return (
      <div className="space-y-4">
        <BackLink />
        <DetailSkeleton />
      </div>
    )
  }

  // Generic error ONLY when there is no cached payload - a transient
  // background-poll blip must not blank a page the user is reading (same
  // posture as the clients list; the header API dot signals the outage).
  if (!data) {
    return (
      <div className="space-y-4">
        <BackLink />
        <Alert variant="error">
          Could not load this client. Retrying automatically...
        </Alert>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <BackLink />
      <HeaderCard client={data} />
      <DuplicateNotice client={data} />
      <Section title="AI sales summary">
        <SalesSummary entries={data.sales_summary} />
      </Section>
      <Section title="Platforms">
        <PlatformGrid client={data} />
      </Section>
      <Section title="Recent actions">
        <ActionHistory actions={data.actions} />
      </Section>
    </div>
  )
}

function BackLink() {
  return (
    <Link
      href="/clients"
      className="text-sm text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
    >
      &larr; All clients
    </Link>
  )
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section>
      <h2 className="mb-3 text-sm font-medium uppercase tracking-wide text-muted-foreground">
        {title}
      </h2>
      {children}
    </section>
  )
}

function HeaderCard({ client }: { client: ClientDetail }) {
  const primary = client.business_name || client.contact_name || client.email
  return (
    <div className="rounded-md border border-border bg-card p-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="truncate text-2xl font-bold" title={primary}>
            {primary}
          </h1>
          <div className="mt-1 text-sm text-muted-foreground">
            {[client.contact_name, client.email, client.phone]
              .filter(Boolean)
              .join(' · ')}
          </div>
          <div className="mt-1 text-xs text-muted-foreground">
            Legal entity: {client.legal_entity}
          </div>
          {/*
            S1-26c: this signing was auto-linked into an existing client's GHL
            sub-account as a returning client. Shown because an auto-link is a
            merge decision the automation made on its own - if the identity key
            matched two businesses that only LOOK alike, this line is the only
            place a human would notice.
          */}
          {client.parent_client_id && (
            <div className="mt-1 text-xs text-muted-foreground">
              Returning client - shares a sub-account with{' '}
              <Link
                href={`/clients/${client.parent_client_id}`}
                className="underline underline-offset-4 hover:text-foreground"
              >
                the original client record
              </Link>
            </div>
          )}
        </div>
        <div className="flex flex-col items-end gap-1">
          <Badge variant="neutral">{stepLabel(client.current_step)}</Badge>
          <span className="text-xs text-muted-foreground">
            {formatTimeInStep(client.step_entered_at)} in step
          </span>
        </div>
      </div>
    </div>
  )
}

/**
 * S1-26c: surface an uncorroborated returning-client match.
 *
 * The worker never auto-merges a match it cannot prove (an identity-key
 * collision whose full names diverge, or a GHL location found by email whose
 * name/postcode do not corroborate). It provisions this client its own
 * sub-account and raises the flag instead. That decision is only safe if a
 * human can see it, so the notice names the candidate - the sibling client, or
 * the GHL location id - rather than leaving someone to search the agency.
 *
 * Renders nothing on the normal path.
 */
function DuplicateNotice({ client }: { client: ClientDetail }) {
  if (!client.possible_duplicate) {
    return null
  }
  return (
    <Alert variant="warning">
      <div className="font-medium">Possible duplicate - needs review</div>
      <p className="mt-1">
        A returning-client match was found but could not be confirmed, so this
        client was given its own GHL sub-account rather than being merged.
      </p>
      {client.possible_duplicate_of && (
        <p className="mt-2">
          Candidate client:{' '}
          <Link
            href={`/clients/${client.possible_duplicate_of}`}
            className="underline underline-offset-4"
          >
            {client.possible_duplicate_of}
          </Link>
        </p>
      )}
      {client.possible_duplicate_ghl_id && (
        <p className="mt-2">
          Candidate GHL sub-account:{' '}
          <code className="rounded bg-black/20 px-1">
            {client.possible_duplicate_ghl_id}
          </code>
        </p>
      )}
    </Alert>
  )
}

function SalesSummary({ entries }: { entries: KnowledgeEntry[] }) {
  if (entries.length === 0) {
    return (
      <Alert variant="info">
        No sales summary yet - it appears here automatically once the sales
        call is processed.
      </Alert>
    )
  }

  const byKey = new Map(entries.map((e) => [e.key, e]))
  // Set-dedupe the unknown keys: duplicate keys in a batch would otherwise
  // render twice with colliding React keys.
  const ordered = [
    ...SUMMARY_KEY_ORDER.filter((k) => byKey.has(k)),
    ...new Set(
      entries.map((e) => e.key).filter((k) => !SUMMARY_KEY_ORDER.includes(k)),
    ),
  ]

  return (
    <div className="space-y-4 rounded-md border border-border bg-card p-6">
      {ordered.map((key) => (
        <SummaryField key={key} entry={byKey.get(key)!} />
      ))}
    </div>
  )
}

function SummaryField({ entry }: { entry: KnowledgeEntry }) {
  return (
    <div>
      <div className="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">
        {humanizeToken(entry.key)}
      </div>
      <SummaryValue entry={entry} />
    </div>
  )
}

function SummaryValue({ entry }: { entry: KnowledgeEntry }) {
  const { key, value } = entry

  if (key === 'budget_range_usd' && value && typeof value === 'object') {
    const range = value as { min?: number; max?: number; currency?: string }
    if (range.min != null && range.max != null) {
      // Fixed locale so the rendering (and the e2e assertion on it) does not
      // drift with the browser locale.
      return (
        <p className="text-sm">
          {range.min.toLocaleString('en-US')} - {range.max.toLocaleString('en-US')}{' '}
          {range.currency ?? 'USD'} / month
        </p>
      )
    }
  }

  if (key === 'notable_quotes' && Array.isArray(value)) {
    if (value.length === 0) return <EmptyValue />
    return (
      <div className="space-y-2">
        {(value as unknown[]).map((raw, i) => {
          // JSONB can legally carry [null] or ["raw string"] - one malformed
          // element must degrade, not white-screen the page.
          const quote =
            raw && typeof raw === 'object' ? (raw as Record<string, unknown>) : {}
          const ts = quote.timestamp_seconds
          return (
            <blockquote
              key={i}
              className="border-l-2 border-border pl-3 text-sm text-muted-foreground"
            >
              &ldquo;{String(quote.quote ?? '')}&rdquo;
              <span className="ml-2 text-xs">
                - {String(quote.speaker ?? 'Unknown')}
                {typeof ts === 'number' &&
                  Number.isFinite(ts) &&
                  ts >= 0 &&
                  ` at ${formatTimestamp(ts)}`}
              </span>
            </blockquote>
          )
        })}
      </div>
    )
  }

  if (Array.isArray(value)) {
    if (value.length === 0) return <EmptyValue />
    return (
      <ul className="list-inside list-disc space-y-0.5 text-sm">
        {value.map((item, i) => (
          <li key={i}>{typeof item === 'string' ? item : JSON.stringify(item)}</li>
        ))}
      </ul>
    )
  }

  if (typeof value === 'string') return <p className="text-sm">{value}</p>

  // Generic fallback (unknown key shapes): prefer the prose rendering.
  if (entry.value_text) return <p className="text-sm">{entry.value_text}</p>
  return (
    <pre className="overflow-x-auto rounded bg-muted px-2 py-1 text-xs text-muted-foreground">
      {JSON.stringify(value, null, 2)}
    </pre>
  )
}

function EmptyValue() {
  return <p className="text-sm text-muted-foreground">None noted</p>
}

function formatTimestamp(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${String(s).padStart(2, '0')}`
}

function PlatformGrid({ client }: { client: ClientDetail }) {
  return (
    <div className="flex flex-wrap gap-2">
      {PLATFORM_LINKS.map((platform) => {
        const id = client[platform.key]
        if (typeof id !== 'string' || !id) {
          // No opacity here: dimming the already-muted text drops contrast
          // below WCAG AA (4.5:1); the dashed border carries the "inactive"
          // signal instead.
          return (
            <span
              key={platform.key}
              className="inline-flex items-center rounded-full border border-dashed border-border px-3 py-1 text-xs text-muted-foreground"
            >
              {platform.label}: not connected
            </span>
          )
        }
        if (platform.url) {
          return (
            <a
              key={platform.key}
              href={platform.url(id)}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 rounded-full border border-border bg-muted px-3 py-1 text-xs text-foreground underline-offset-4 hover:underline"
            >
              {platform.label} <span aria-hidden>&#8599;</span>
              <span className="sr-only">(opens in a new tab)</span>
            </a>
          )
        }
        return (
          <span
            key={platform.key}
            title={id}
            className="inline-flex items-center rounded-full border border-border bg-muted px-3 py-1 text-xs text-muted-foreground"
          >
            {platform.label}: connected
          </span>
        )
      })}
    </div>
  )
}

function ActionHistory({ actions }: { actions: ClientDetail['actions'] }) {
  if (actions.length === 0) {
    return <Alert variant="info">No platform actions recorded yet.</Alert>
  }
  return (
    <div className="overflow-x-auto rounded-md border border-border">
      <table className="w-full text-sm">
        <thead className="bg-muted text-muted-foreground">
          <tr>
            {['Action', 'Status', 'Started', 'Run'].map((col) => (
              <th
                key={col}
                scope="col"
                className="px-4 py-2 text-left text-xs font-medium uppercase tracking-wide"
              >
                {col}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {actions.map((action, i) => (
            <tr key={i} className="border-t border-border">
              <td className="px-4 py-3">
                <span className="font-medium">{humanizeToken(action.platform)}</span>
                <span className="text-muted-foreground"> · {humanizeToken(action.action)}</span>
                {action.last_error && (
                  <div
                    className="max-w-[360px] truncate text-xs text-red-200"
                    title={action.last_error}
                  >
                    {action.last_error}
                  </div>
                )}
              </td>
              <td className="px-4 py-3">
                <Badge variant={actionStatusVariant(action.status)}>
                  {humanizeToken(action.status)}
                </Badge>
              </td>
              <td className="px-4 py-3 text-muted-foreground">
                {startedAgo(action.started_at)}
              </td>
              <td className="px-4 py-3">
                <RunCell runId={action.inngest_run_id} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** "2h ago" / "just now" / "-" - never "just now ago" or "- ago". */
function startedAgo(iso: string | null): string {
  if (!iso) return '-'
  const elapsed = formatTimeInStep(iso)
  if (elapsed === '-' || elapsed === 'just now') return elapsed
  return `${elapsed} ago`
}

function RunCell({ runId }: { runId: string | null }) {
  if (!runId) return <span className="text-xs text-muted-foreground">-</span>
  const url = inngestRunUrl(runId)
  if (!url) {
    // Fail-closed: NEXT_PUBLIC_INNGEST_URL not configured in this build -
    // show the raw run id rather than a link to someone's localhost.
    return (
      <span className="text-xs text-muted-foreground" title={runId}>
        run {runId.slice(0, 8)}...
      </span>
    )
  }
  return (
    <a
      href={url}
      target="_blank"
      rel="noreferrer"
      data-testid="inngest-run-link"
      className="text-xs text-foreground underline-offset-4 hover:underline"
    >
      View run <span aria-hidden>&#8599;</span>
      <span className="sr-only">in the Inngest UI (opens in a new tab)</span>
    </a>
  )
}

function DetailSkeleton() {
  return (
    <div className="space-y-6" role="status" aria-label="Loading client details">
      {[0, 1, 2].map((block) => (
        <div
          key={block}
          aria-hidden
          className="rounded-md border border-border bg-card p-6"
        >
          <div className="h-4 w-40 animate-pulse rounded bg-muted" />
          <div className="mt-3 h-4 w-64 animate-pulse rounded bg-muted" />
        </div>
      ))}
      <span className="sr-only">Loading client details...</span>
    </div>
  )
}
