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

import createClient, { type Client, type Middleware } from "openapi-fetch";

import type { paths } from "./generated/api-types";

export type ApiClient = Client<paths>;

export interface ApiClientOptions {
  /**
   * Called when a data request returns 401 (the session expired mid-use).
   * `/me` and `/auth/*` are excluded here - they own their own 401 handling
   * (the dashboard's bootstrap guard and the login form), so invoking this on
   * them would loop. The dashboard wires this to a redirect to /login.
   */
  onUnauthorized?: (path: string) => void;
}

/**
 * Build a typed API client pointed at `baseUrl`.
 *
 * @param baseUrl Origin of the API, e.g. `http://localhost:8000` in dev or
 *   the staging API host in deployed environments. No trailing slash.
 * @param opts Optional hooks; `onUnauthorized` fires on a 401 from any data
 *   endpoint (openapi-fetch middleware lives here because this package owns the
 *   dependency).
 */
export function createApiClient(
  baseUrl: string,
  opts: ApiClientOptions = {},
): ApiClient {
  const client = createClient<paths>({ baseUrl, credentials: "include" });
  const onUnauthorized = opts.onUnauthorized;
  if (onUnauthorized) {
    const middleware: Middleware = {
      async onResponse({ request, response }) {
        if (response.status === 401) {
          // Strip scheme+host and any query/hash to get the pathname, without
          // relying on the `URL` global (this package targets both node + the
          // browser and the tsconfig lib does not declare it).
          const path = request.url
            .replace(/^[a-z]+:\/\/[^/]+/i, "")
            .split(/[?#]/)[0];
          if (path !== "/me" && !path.startsWith("/auth/")) {
            onUnauthorized(path);
          }
        }
        return response;
      },
    };
    client.use(middleware);
  }
  return client;
}

/**
 * Default poll interval (ms) for active dashboard views.
 *
 * The card specifies 5-10s polling via TanStack Query; 7s sits in the middle
 * of that band. Views opt in by passing this as `refetchInterval` on the
 * queries that should stay live, rather than polling every query globally.
 */
export const POLL_INTERVAL_MS = 7000;
