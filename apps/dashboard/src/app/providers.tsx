'use client'

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useState } from 'react'

/**
 * Client-side context providers for the dashboard (S1-17).
 *
 * Holds the TanStack Query client. The `QueryClient` is created once per
 * browser session via `useState` (not at module scope) so each request gets
 * an isolated cache and client state never leaks across users on the server.
 *
 * Polling is opt-in per query (see POLL_INTERVAL_MS on active views) rather
 * than a global `refetchInterval`, so one-shot queries like `/me` are not
 * refetched on a timer.
 */
export function Providers({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 5_000,
            retry: 1,
            refetchOnWindowFocus: true,
          },
        },
      }),
  )

  return (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
}
