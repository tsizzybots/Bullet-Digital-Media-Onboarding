import { execFileSync } from 'node:child_process'
import path from 'node:path'

import { expect, test } from '@playwright/test'

import { getFixtures } from './fixtures'

const API_DIR = path.resolve(__dirname, '../../api')

/**
 * S1-31: the clients board renders every seeded client, and its 10s poll picks
 * up a client inserted server-side after the page is already open.
 *
 * The three baseline clients are provisioned by global-setup
 * (scripts/seed_e2e_clients.py). The fourth is inserted mid-test via
 * scripts/seed_one_client.py - directly against the DB, NOT through the UI -
 * which is exactly the "appears via polling" path the card calls for.
 */
test('clients list renders seeded clients and polls in a new one', async ({
  page,
}) => {
  const fixtures = getFixtures()

  // Log in -> land on /clients.
  await page.goto('/login')
  await page.getByLabel('Email').fill(fixtures.login.email)
  await page.getByLabel('Password').fill(fixtures.login.password)
  await page.getByRole('button', { name: /log in/i }).click()
  await expect(page).toHaveURL(/\/clients$/)

  // The three seeded clients render in their steps.
  await expect(page.getByText('E2E Alpha Gym')).toBeVisible()
  await expect(page.getByText('E2E Bravo Fitness')).toBeVisible()
  await expect(page.getByText('E2E Charlie Studio')).toBeVisible()

  // Insert a fourth client server-side (no page reload).
  execFileSync(
    'uv',
    [
      'run',
      'python',
      'scripts/seed_one_client.py',
      '--doc-id',
      'e2e-delta',
      '--business',
      'E2E Delta Strength',
      '--email',
      'delta@e2e.example',
      '--step',
      'portal',
    ],
    { cwd: API_DIR, env: process.env, encoding: 'utf-8' },
  )

  // The 10s poll must surface it without a manual refresh. Allow a generous
  // window (poll interval + fetch + render) before failing.
  await expect(page.getByText('E2E Delta Strength')).toBeVisible({
    timeout: 20_000,
  })
})
