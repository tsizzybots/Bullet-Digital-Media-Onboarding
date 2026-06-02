import { readFileSync } from 'node:fs'
import path from 'node:path'

/**
 * Typed reader for the seeded E2E fixtures (S1-18).
 *
 * `e2e/global-setup.ts` writes e2e/.fixtures.json from the seed script output
 * before any spec runs. Specs call `getFixtures()` to read the deterministic
 * credentials + confirmation token.
 */
export interface E2EFixtures {
  login: {
    email: string
    password: string
  }
  confirm: {
    email: string
    password: string
    token: string
  }
}

const FIXTURES_PATH = path.resolve(__dirname, '.fixtures.json')

export function getFixtures(): E2EFixtures {
  let raw: string
  try {
    raw = readFileSync(FIXTURES_PATH, 'utf-8')
  } catch {
    throw new Error(
      `Missing ${FIXTURES_PATH}. The Playwright globalSetup should have written ` +
        `it from the seed script; did global-setup run?`,
    )
  }
  return JSON.parse(raw) as E2EFixtures
}
