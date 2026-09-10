import { defineConfig, devices } from '@playwright/test'

/**
 * Playwright E2E config for the dashboard (S1-18).
 *
 * Boots BOTH the FastAPI backend (:8000) and the Next.js dashboard (:3000)
 * via the `webServer` array, then runs the auth specs against the real
 * stack. `globalSetup` seeds two deterministic fixture users before any test
 * runs (see e2e/global-setup.ts).
 *
 * The suite is NOT fully parallel: the seeded users are shared mutable state
 * (the confirm user transitions from unconfirmed -> confirmed), so we pin to a
 * single worker and run files serially.
 */
export default defineConfig({
  testDir: './e2e',
  testMatch: '**/*.spec.ts',
  // Seed users are shared state across specs; never run them concurrently.
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: 'list',
  globalSetup: './e2e/global-setup.ts',
  use: {
    baseURL: 'http://localhost:3000',
    trace: 'on-first-retry',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  // Playwright boots both servers and waits for each `url` to respond before
  // starting the run. Locally we reuse already-running dev servers; CI always
  // boots fresh.
  webServer: [
    {
      // Inngest dev server. REQUIRED, not optional (review round 7 discovery):
      // the attach endpoint commits, then emits `transcript.linked`, and with
      // nothing on :8288 the emit raises AFTER the DB write - the API returns
      // 500, the UI shows "Attach failed", and the spec's green run had been
      // depending on an ambient dev server someone happened to have running.
      // Booting it here makes the stack self-contained.
      command: 'npx inngest-cli dev --no-discovery',
      url: 'http://localhost:8288',
      reuseExistingServer: !process.env.CI,
      timeout: 180_000,
      stdout: 'pipe',
    },
    {
      // FastAPI backend. `uv run` resolves the project venv from apps/api.
      command: 'uv run uvicorn bullet_api.main:app --host 127.0.0.1 --port 8000',
      cwd: '../api',
      url: 'http://127.0.0.1:8000/healthz',
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      stdout: 'pipe',
      env: {
        // DATABASE_URL is provided by the Make target / CI runner against a
        // migrated DB; pass the process value straight through.
        DATABASE_URL: process.env.DATABASE_URL ?? '',
        // The dashboard origin must be allowed so credentialed fetches work.
        CORS_ALLOW_ORIGINS: 'http://localhost:3000',
        // We never send mail during E2E; an empty key keeps the email client
        // inert without crashing app import/startup.
        RESEND_API_KEY: '',
        // Put the Inngest SDK in dev mode so importing bullet_api.main does not
        // raise SigningKeyMissingError (no signing key locally). Mirrors what
        // conftest.py and the `codegen` Make target already do. (This comment
        // used to claim the dev server itself was "NOT required for these
        // specs" - false since S1-27a: the attach spec's endpoint emits, and
        // the dev server above is what receives it.)
        INNGEST_DEV: '1',
      },
    },
    {
      // Next.js dashboard dev server.
      command: 'pnpm dev',
      url: 'http://localhost:3000/login',
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      env: {
        NEXT_PUBLIC_API_URL: 'http://localhost:8000',
        // Pinned so the client-detail spec's exact Inngest-href assertion is
        // self-contained - a developer's .env.local value (or a reused dev
        // server with a different one) must not change what the spec sees.
        NEXT_PUBLIC_INNGEST_URL: 'http://localhost:8288',
      },
    },
  ],
})
