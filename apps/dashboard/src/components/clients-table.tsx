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

type ClientRow = components['schemas']['ClientListItem']

// 10s polling per the S1-31 card (the shared POLL_INTERVAL_MS is 7s; this view
// is explicit so the value matches the spec and the e2e wait window).
const CLIENTS_POLL_INTERVAL_MS = 10_000

const COLUMNS = ['Client', 'Step', 'Time in step', 'Last action'] as const

export function ClientsTable() {
  const { data, isPending, isError } = useQuery({
    queryKey: ['clients'],
    queryFn: async () => {
      // Fail fast on a slow/stuck request rather than holding it open under
      // polling load (pairs with the DB-side statement_timeout).
      const { data, error } = await api.GET('/clients', {
        signal: AbortSignal.timeout(8_000),
      })
      if (error || !data) throw new Error('clients request failed')
      return data
    },
    refetchInterval: CLIENTS_POLL_INTERVAL_MS,
  })

  // Only blank the board when we have nothing to show. On a transient
  // background-poll failure we keep the last good rows on screen (the header
  // API-status dot already signals the outage), then recover on the next poll.
  if (isError && !data) {
    return (
      <Alert variant="error">
        Could not load clients. Retrying automatically...
      </Alert>
    )
  }

  if (isPending || !data) {
    // Live region lives OUTSIDE the table: a tr with role="status" would
    // break the required rowgroup>row ARIA structure (axe flags it).
    return (
      <div role="status" aria-label="Loading clients">
        <TableFrame>{<SkeletonRows />}</TableFrame>
        <span className="sr-only">Loading clients...</span>
      </div>
    )
  }

  if (data.clients.length === 0) {
    return (
      <Alert variant="info">
        No clients yet - clients appear here once an agreement is signed.
      </Alert>
    )
  }

  return (
    <TableFrame>
      {data.clients.map((client) => (
        <ClientRowView key={client.id} client={client} />
      ))}
    </TableFrame>
  )
}

function TableFrame({ children }: { children: ReactNode }) {
  return (
    <div className="overflow-x-auto rounded-md border border-border">
      <table className="w-full text-sm">
        <thead className="bg-muted text-muted-foreground">
          <tr>
            {COLUMNS.map((col) => (
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
        <tbody>{children}</tbody>
      </table>
    </div>
  )
}

function ClientRowView({ client }: { client: ClientRow }) {
  const primary = client.business_name || client.contact_name || client.email

  return (
    <tr className="border-t border-border transition-colors hover:bg-muted/50">
      <td className="px-4 py-3">
        <Link
          href={`/clients/${client.id}`}
          title={primary}
          className="block max-w-[240px] truncate font-medium text-foreground underline-offset-4 hover:underline"
        >
          {primary}
        </Link>
        <div
          className="max-w-[240px] truncate text-xs text-muted-foreground"
          title={client.email}
        >
          {client.email}
        </div>
      </td>
      <td className="px-4 py-3">
        <Badge variant="neutral">{stepLabel(client.current_step)}</Badge>
      </td>
      <td className="px-4 py-3 text-muted-foreground">
        {formatTimeInStep(client.step_entered_at)}
      </td>
      <td className="px-4 py-3">
        {client.last_action_status ? (
          <div className="flex flex-col items-start gap-1">
            <Badge variant={actionStatusVariant(client.last_action_status)}>
              {humanizeToken(client.last_action_status)}
            </Badge>
            {client.last_action_platform && (
              <span className="text-xs text-muted-foreground">
                {humanizeToken(client.last_action_platform)} ·{' '}
                {humanizeToken(client.last_action)}
              </span>
            )}
          </div>
        ) : (
          <span className="text-xs text-muted-foreground">No actions yet</span>
        )}
      </td>
    </tr>
  )
}

function SkeletonRows() {
  return (
    <>
      {[0, 1, 2].map((row) => (
        <tr key={row} aria-hidden className="border-t border-border">
          {COLUMNS.map((col) => (
            <td key={col} className="px-4 py-3">
              <div className="h-4 w-24 animate-pulse rounded bg-muted" />
            </td>
          ))}
        </tr>
      ))}
    </>
  )
}
