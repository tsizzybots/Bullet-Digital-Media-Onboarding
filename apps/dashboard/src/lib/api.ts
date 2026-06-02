import { createApiClient } from '@bullet/shared'

/**
 * Singleton typed API client for the dashboard.
 *
 * Base URL comes from `NEXT_PUBLIC_API_URL` (inlined at build time by Next;
 * declared in render.yaml for staging). Falls back to the local API dev
 * server so `pnpm dev` works with no extra env setup.
 */
export const api = createApiClient(
  process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000',
)
