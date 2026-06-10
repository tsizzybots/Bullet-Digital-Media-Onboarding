import { execFileSync } from 'node:child_process'
import { writeFileSync } from 'node:fs'
import path from 'node:path'

/**
 * Playwright global setup (S1-18).
 *
 * Runs the deterministic E2E seed script (apps/api/scripts/seed_e2e_users.py)
 * once, before any spec, to provision the two fixture users. The script prints
 * human/log output on stderr and EXACTLY ONE line of JSON on stdout; we capture
 * stdout, take the last non-empty line, parse it, and persist it to
 * e2e/.fixtures.json so specs can read the credentials + confirmation token
 * (see e2e/fixtures.ts).
 *
 * Requires DATABASE_URL in the environment and a migrated DB; the Make target /
 * CI runner provides both.
 */
const FIXTURES_PATH = path.resolve(__dirname, '.fixtures.json')
const API_DIR = path.resolve(__dirname, '../../api')

export default async function globalSetup(): Promise<void> {
  let stdout: string
  try {
    stdout = execFileSync('uv', ['run', 'python', 'scripts/seed_e2e_users.py'], {
      cwd: API_DIR,
      env: process.env,
      encoding: 'utf-8',
    })
  } catch (error) {
    throw new Error(
      `E2E seed script failed to run. Ensure DATABASE_URL is set and the DB is ` +
        `migrated (make db-upgrade). Underlying error: ${
          error instanceof Error ? error.message : String(error)
        }`,
    )
  }

  // The seed emits one JSON line on stdout; logs go to stderr. Defensively
  // take the LAST non-empty stdout line in case anything else leaks through.
  const lastLine = stdout
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line.length > 0)
    .at(-1)

  if (!lastLine) {
    throw new Error('E2E seed script produced no stdout to parse for fixtures.')
  }

  let fixtures: unknown
  try {
    fixtures = JSON.parse(lastLine)
  } catch (error) {
    throw new Error(
      `Could not parse E2E seed JSON from stdout. Got: ${JSON.stringify(
        lastLine,
      )}. Parse error: ${error instanceof Error ? error.message : String(error)}`,
    )
  }

  writeFileSync(FIXTURES_PATH, JSON.stringify(fixtures, null, 2), 'utf-8')

  // Seed the deterministic clients the S1-31 list spec asserts on. No JSON to
  // parse here - the script just upserts rows (idempotent) and logs to stderr.
  try {
    execFileSync('uv', ['run', 'python', 'scripts/seed_e2e_clients.py'], {
      cwd: API_DIR,
      env: process.env,
      encoding: 'utf-8',
    })
  } catch (error) {
    throw new Error(
      `E2E client seed failed. Ensure DATABASE_URL is set and the DB is ` +
        `migrated (make db-upgrade). Underlying error: ${
          error instanceof Error ? error.message : String(error)
        }`,
    )
  }
}
