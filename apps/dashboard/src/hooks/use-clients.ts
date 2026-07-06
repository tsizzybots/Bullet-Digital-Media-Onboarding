'use client'

import type { components } from '@bullet/shared'
import { useQuery } from '@tanstack/react-query'

import { api } from '@/lib/api'

/** One client row from `GET /clients`, from the generated OpenAPI types. */
export type ClientListItem = components['schemas']['ClientListItem']

/**
 * The single shared query key for the clients list (S1-27c). The S1-31 board,
 * the S1-27a attach picker, and their cache invalidations all reference THIS
 * constant, so those call sites cannot drift apart. (It cannot stop a NEW call
 * site from hand-writing `['clients']` again - the guard against that is using
 * `useClients()` / this constant everywhere, not the constant itself.)
 */
export const clientsQueryKey = ['clients'] as const

/** Poll interval (10s) shared by the clients board + the transcript picker. */
export const CLIENTS_POLL_INTERVAL_MS = 10_000

/**
 * Clients-list hook (S1-27c). The single typed source for `GET /clients`, used
 * by both the S1-31 board and the S1-27a attach picker, so the query key + fetch
 * shape live in exactly one place (previously each component re-spelled an
 * untyped `['clients']` key + inline queryFn). Fails fast on a slow request (8s
 * abort) rather than holding it open under polling load, and polls.
 */
export function useClients() {
  return useQuery({
    queryKey: clientsQueryKey,
    queryFn: async () => {
      const { data, error } = await api.GET('/clients', {
        signal: AbortSignal.timeout(8_000),
      })
      if (error || !data) throw new Error('clients request failed')
      return data
    },
    refetchInterval: CLIENTS_POLL_INTERVAL_MS,
  })
}
