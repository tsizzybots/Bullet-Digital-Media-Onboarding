import type { NextConfig } from 'next'
import { withSentryConfig } from '@sentry/nextjs'

const nextConfig: NextConfig = {
  output: 'standalone',
  // @bullet/shared ships TypeScript source (its main/types point at
  // src/index.ts), so Next must transpile it rather than expecting
  // pre-built JS. This keeps the dashboard build free of any separate
  // shared-package build step.
  transpilePackages: ['@bullet/shared'],

  // Same-origin API proxy. The session cookie is HttpOnly + host-scoped, so it
  // can only be read by the dashboard's own domain (and `.onrender.com` is a
  // public suffix, so a shared parent-domain cookie is impossible). When the
  // dashboard and API live on different hosts, the browser must therefore talk
  // to the API *through the dashboard's own origin* so the session cookie lands
  // on - and is sent back from - the dashboard host, and the middleware can read
  // it. Set `NEXT_PUBLIC_API_URL=/api/backend` (relative) so the browser hits
  // this path, and `API_PROXY_TARGET=<api origin>` so Next proxies it server-
  // side. Locally both are unset: `NEXT_PUBLIC_API_URL` falls back to
  // localhost:8000 (same host as the dashboard, so cookies already work) and
  // this rewrite is a no-op.
  async rewrites() {
    const target = process.env.API_PROXY_TARGET
    if (!target) return []
    return [{ source: '/api/backend/:path*', destination: `${target}/:path*` }]
  },
}

// Wrap with Sentry's Next.js plugin (S1-20, Slice B). Source-map upload only
// runs when SENTRY_AUTH_TOKEN is present at build time; without it the plugin
// prints a benign "no auth token, skipping upload" notice and the build still
// succeeds. Runtime capture stays disabled-by-default via the empty-DSN guard
// in the Sentry config files.
export default withSentryConfig(nextConfig, {
  org: process.env.SENTRY_ORG,
  project: process.env.SENTRY_PROJECT,
  authToken: process.env.SENTRY_AUTH_TOKEN,

  // Quiet during local/dev builds; verbose in CI.
  silent: !process.env.CI,

  // Also upload source maps referenced by the client bundle.
  widenClientFileUpload: true,

  sourcemaps: {
    // Remove emitted .map files after upload so they are not served publicly.
    deleteSourcemapsAfterUpload: true,
  },

  release: {
    name: process.env.RENDER_GIT_COMMIT,
  },
})
