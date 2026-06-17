// Inngest UI deep-links (S1-32). The API returns raw `inngest_run_id`s; the
// dashboard owns URL policy via NEXT_PUBLIC_INNGEST_URL.
//
// Fail-closed on misconfig: in a production build with the var unset, empty,
// or whitespace-only (the value is trimmed, so "   " collapses to ""),
// `inngestRunUrl` returns null and the UI renders a non-link run id instead of
// a link to the operator's own localhost. Only dev builds default to the
// docker dev server.
//
// URL shapes differ per target: the local Inngest DEV SERVER uses
// `/run?runID=<id>`; Inngest CLOUD (app.inngest.com) uses path-shaped
// `/runs/<id>` under the env URL pasted into the Render slot.

const RAW_BASE = (process.env.NEXT_PUBLIC_INNGEST_URL ?? '').trim()
const INNGEST_BASE =
  RAW_BASE ||
  (process.env.NODE_ENV === 'development' ? 'http://localhost:8288' : null)

/** URL of one run in the Inngest UI, or null when not configured (prod build
 * without NEXT_PUBLIC_INNGEST_URL - callers must render a non-link). */
export function inngestRunUrl(runId: string): string | null {
  if (!INNGEST_BASE) return null
  const base = INNGEST_BASE.replace(/\/+$/, '')
  if (base.includes('app.inngest.com')) {
    return `${base}/runs/${encodeURIComponent(runId)}`
  }
  return `${base}/run?runID=${encodeURIComponent(runId)}`
}
