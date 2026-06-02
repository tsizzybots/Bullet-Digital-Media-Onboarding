// Public surface of @bullet/shared.
//
// The typed REST client and its generated types, produced from apps/api's
// OpenAPI document by the S1-17 codegen pipeline (`make codegen`). Regenerate
// with `pnpm --filter @bullet/shared codegen` after changing the API; CI fails
// on drift between the committed artifacts and the live API contract.

export { createApiClient, POLL_INTERVAL_MS, type ApiClient } from "./client";
export type { paths, components, operations } from "./generated/api-types";
