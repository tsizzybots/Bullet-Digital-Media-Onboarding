'use client'

import { POLL_INTERVAL_MS } from '@bullet/shared'
import { useQuery } from '@tanstack/react-query'

import { api } from '@/lib/api'

/**
 * S1-17 acceptance sample: a live query against the API's `/healthz`,
 * polled every POLL_INTERVAL_MS through TanStack Query.
 *
 * `data` is typed `{ status: string }` end-to-end - the type is generated
 * from the API's OpenAPI document, so there is no `any` anywhere in this
 * path. This doubles as a small API-status indicator; S1-31 folds a richer
 * health / build-info surface into the real dashboard chrome.
 */
export function HealthStatus() {
  const { data, isPending, isError } = useQuery({
    queryKey: ['healthz'],
    queryFn: async () => {
      const { data, error } = await api.GET('/healthz')
      if (error || !data) throw new Error('healthz request failed')
      return data
    },
    refetchInterval: POLL_INTERVAL_MS,
  })

  let dot: string
  let label: string
  if (isError) {
    dot = 'bg-red-500'
    label = 'unreachable'
  } else if (isPending || !data) {
    dot = 'bg-yellow-500'
    label = 'checking...'
  } else {
    dot = 'bg-green-500'
    label = data.status
  }

  return (
    <span className="inline-flex items-center gap-2 text-sm text-muted-foreground">
      <span className={`h-2 w-2 rounded-full ${dot}`} aria-hidden />
      API: {label}
    </span>
  )
}
