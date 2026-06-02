'use client'

import type { components } from '@bullet/shared'
import { useQuery } from '@tanstack/react-query'

import { api } from '@/lib/api'

/**
 * The authenticated user's profile, as returned by `GET /me`.
 *
 * Derived from the generated OpenAPI types so the fields stay in lockstep with
 * the API contract - never hand-redefined.
 */
export type Me = components['schemas']['MeResponse']

/**
 * Current-user hook for the dashboard (S1-18).
 *
 * Wraps `GET /me` in TanStack Query. `retry: false` is deliberate: a 401 must
 * surface immediately as `isError` so the auth guard can redirect to login
 * instead of silently retrying an unauthenticated request.
 */
export function useMe() {
  const { data, isLoading, isError, refetch } = useQuery<Me>({
    queryKey: ['me'],
    queryFn: async () => {
      const { data, error } = await api.GET('/me')
      if (error) throw error
      return data
    },
    retry: false,
  })

  return { user: data, isLoading, isError, refetch }
}
