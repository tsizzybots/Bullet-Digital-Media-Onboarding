'use client'

import type { components } from '@bullet/shared'
import * as Sentry from '@sentry/nextjs'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import type { ReactNode } from 'react'

import { Alert } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { ClientCombobox, type ClientOption } from '@/components/ui/client-combobox'
import { api } from '@/lib/api'
import { formatCharCount, formatDateTime } from '@/lib/format'

type UnlinkedTranscript = components['schemas']['UnlinkedTranscriptItem']

// 10s poll, matching the clients board (S1-31). A freshly auto-linked or
// manually-attached transcript drops off the list on the next poll / on the
// mutation's cache invalidation.
const TRANSCRIPTS_POLL_INTERVAL_MS = 10_000

const COLUMNS = ['Call', 'When', 'Size', 'Attach to client'] as const

/** Carries the HTTP status so the row can both message AND refresh on a stale 409/404. */
class AttachError extends Error {
  constructor(
    readonly status: number,
    message: string,
    options?: ErrorOptions,
  ) {
    super(message, options)
  }
}

function attachErrorMessage(status: number): string {
  switch (status) {
    case 409:
      return 'Already linked elsewhere - refreshing the list.'
    case 404:
      return 'This transcript no longer exists - refreshing the list.'
    case 400:
      return 'That client could not be found. Pick another.'
    default:
      return 'Attach failed. Please try again.'
  }
}

export function UnlinkedTranscripts() {
  const { data, isPending, isError } = useQuery({
    queryKey: ['transcripts', 'unlinked'],
    queryFn: async () => {
      const { data, error } = await api.GET('/transcripts/unlinked', {
        signal: AbortSignal.timeout(8_000),
      })
      if (error || !data) throw new Error('unlinked transcripts request failed')
      return data
    },
    refetchInterval: TRANSCRIPTS_POLL_INTERVAL_MS,
  })

  // Clients for the picker. Same key as the board so it is cached/deduped, and
  // polled so the options do not go stale on a page left open. A background
  // failure is surfaced (below) rather than silently leaving the picker empty -
  // on a page whose whole job is attaching to a client, "no clients" must be
  // distinguishable from "the client list failed to load".
  const clientsQuery = useQuery({
    queryKey: ['clients'],
    queryFn: async () => {
      const { data, error } = await api.GET('/clients', { signal: AbortSignal.timeout(8_000) })
      if (error || !data) throw new Error('clients request failed')
      return data
    },
    refetchInterval: TRANSCRIPTS_POLL_INTERVAL_MS,
  })

  if (isError && !data) {
    return (
      <Alert variant="error">Could not load transcripts. Retrying automatically...</Alert>
    )
  }

  if (isPending || !data) {
    return (
      <div role="status" aria-label="Loading transcripts">
        <TableFrame>
          <SkeletonRows />
        </TableFrame>
        <span className="sr-only">Loading transcripts...</span>
      </div>
    )
  }

  if (data.transcripts.length === 0) {
    return (
      <Alert variant="info">
        All transcripts are linked - nothing waiting to be attached.
      </Alert>
    )
  }

  const clients: ClientOption[] = clientsQuery.data?.clients ?? []
  // Distinguish "client list failed to load" from "no clients" so the empty
  // picker is not a silent trap.
  const clientsUnavailable = clientsQuery.isError && clients.length === 0

  return (
    <div className="space-y-3">
      {clientsUnavailable && (
        <Alert variant="error">
          Could not load the client list - the picker may be empty. Retrying...
        </Alert>
      )}
      <TableFrame>
        {data.transcripts.map((transcript) => (
          <TranscriptRowView key={transcript.id} transcript={transcript} clients={clients} />
        ))}
      </TableFrame>
    </div>
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

function TranscriptRowView({
  transcript,
  clients,
}: {
  transcript: UnlinkedTranscript
  clients: ClientOption[]
}) {
  const queryClient = useQueryClient()
  const [clientId, setClientId] = useState<string | null>(null)
  // Resolve the picked id against the *current* client list. If the 10s poll
  // drops the picked client, this becomes null, so the Attach button gates off
  // rather than firing a doomed 400 with a stale id. Single source of truth for
  // "is a real, still-present client selected".
  const selectedClient = clients.find((client) => client.id === clientId) ?? null

  const attach = useMutation({
    mutationFn: async (selectedClientId: string) => {
      let result
      try {
        result = await api.POST('/transcripts/{transcript_id}/link', {
          params: { path: { transcript_id: transcript.id } },
          body: { client_id: selectedClientId },
          // Fail fast + visibly on a hung request rather than leaving the button
          // stuck on "Attaching...".
          signal: AbortSignal.timeout(8_000),
        })
      } catch (err) {
        // Transport-level failure (timeout / reset / abort) OR a genuine JS bug
        // in the request path: openapi-fetch re-throws the raw fetch error
        // rather than returning `{ error }`. Capture it to Sentry (this is the
        // app's only mutation - without this, a real transport failure or bug is
        // invisible) and preserve it as `cause` before normalising to the
        // friendly, status-0 "Attach failed" copy the row renders.
        Sentry.captureException(err)
        throw new AttachError(0, attachErrorMessage(0), { cause: err })
      }
      if (result.error) {
        throw new AttachError(
          result.response.status,
          attachErrorMessage(result.response.status),
        )
      }
    },
    onSuccess: () => {
      // Row drops off the unlinked list; the client may now show the transcript.
      queryClient.invalidateQueries({ queryKey: ['transcripts', 'unlinked'] })
      queryClient.invalidateQueries({ queryKey: ['clients'] })
    },
    onError: (error) => {
      if (!(error instanceof AttachError)) return
      // A stale row (already linked / gone) self-corrects by refreshing the list.
      if (error.status === 409 || error.status === 404) {
        queryClient.invalidateQueries({ queryKey: ['transcripts', 'unlinked'] })
      }
      // A 400 (unknown client) most likely means a stale picker option; refresh
      // the client list so the picker self-corrects.
      if (error.status === 400) {
        queryClient.invalidateQueries({ queryKey: ['clients'] })
      }
    },
  })

  const emails = transcript.participant_emails

  return (
    <tr className="border-t border-border align-top">
      <td className="px-4 py-3">
        {emails.length > 0 ? (
          <div className="flex max-w-[260px] flex-col gap-0.5">
            {emails.map((email) => (
              <span key={email} className="truncate text-foreground" title={email}>
                {email}
              </span>
            ))}
          </div>
        ) : (
          <span className="text-xs text-muted-foreground">No attendee emails captured</span>
        )}
      </td>
      <td className="px-4 py-3 text-muted-foreground">{formatDateTime(transcript.meeting_start)}</td>
      <td className="px-4 py-3 text-muted-foreground">
        {formatCharCount(transcript.transcript_chars)}
      </td>
      <td className="px-4 py-3">
        <div className="flex items-start gap-2">
          <div className="min-w-[220px]">
            <ClientCombobox
              clients={clients}
              value={clientId}
              onChange={setClientId}
              disabled={attach.isPending}
              inputId={`client-${transcript.id}`}
            />
          </div>
          <Button
            onClick={() => selectedClient && attach.mutate(selectedClient.id)}
            disabled={!selectedClient || attach.isPending}
          >
            {attach.isPending ? 'Attaching...' : 'Attach'}
          </Button>
        </div>
        {attach.isError && (
          <p className="mt-1.5 text-xs text-red-300" role="alert">
            {attach.error.message}
          </p>
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
              <div className="h-4 w-28 animate-pulse rounded bg-muted" />
            </td>
          ))}
        </tr>
      ))}
    </>
  )
}
