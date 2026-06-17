import { createApiClient } from '@bullet/shared'

/**
 * Singleton typed API client for the dashboard.
 *
 * Base URL comes from `NEXT_PUBLIC_API_URL` (inlined at build time by Next;
 * declared in render.yaml for staging). Falls back to the local API dev
 * server so `pnpm dev` works with no extra env setup.
 *
 * `onUnauthorized`: when a data request 401s while a tab is open (the session
 * expired mid-session), route to /login with a `next` back-link instead of
 * leaving the user on a frozen page that silently stopped updating. The shared
 * client already excludes `/me` and `/auth/*`; we additionally no-op if we are
 * already on /login so it cannot loop.
 */
export const api = createApiClient(
  process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000',
  {
    onUnauthorized: () => {
      if (typeof window === 'undefined') return
      if (window.location.pathname.startsWith('/login')) return
      const here = window.location.pathname + window.location.search
      window.location.assign(`/login?next=${encodeURIComponent(here)}`)
    },
  },
)
