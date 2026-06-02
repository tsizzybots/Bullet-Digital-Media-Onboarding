/**
 * Typed REST client for the Bullet API (S1-17).
 *
 * `openapi-fetch` wraps the generated `paths` type so every call site gets
 * exact request/response types with zero `any`. The base URL is injected by
 * the caller (the dashboard reads it from `NEXT_PUBLIC_API_URL`) so this
 * package stays environment-agnostic and reusable from non-Next contexts.
 *
 * `credentials: "include"` makes the browser send the HttpOnly `session`
 * cookie on cross-origin calls - the API's CORS middleware is configured
 * with `allow_credentials=True` to accept it.
 */

import createClient, { type Client } from "openapi-fetch";

import type { paths } from "./generated/api-types";

export type ApiClient = Client<paths>;

/**
 * Build a typed API client pointed at `baseUrl`.
 *
 * @param baseUrl Origin of the API, e.g. `http://localhost:8000` in dev or
 *   the staging API host in deployed environments. No trailing slash.
 */
export function createApiClient(baseUrl: string): ApiClient {
  return createClient<paths>({
    baseUrl,
    credentials: "include",
  });
}

/**
 * Default poll interval (ms) for active dashboard views.
 *
 * The card specifies 5-10s polling via TanStack Query; 7s sits in the middle
 * of that band. Views opt in by passing this as `refetchInterval` on the
 * queries that should stay live, rather than polling every query globally.
 */
export const POLL_INTERVAL_MS = 7000;
