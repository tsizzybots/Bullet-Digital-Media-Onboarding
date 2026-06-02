import { expect, test } from '@playwright/test'

import { getFixtures } from './fixtures'

test('unconfirmed user confirms via the link, then logs in', async ({ page }) => {
  // Read fixtures lazily at runtime: globalSetup writes .fixtures.json before
  // the run, but `playwright test --list` collects specs WITHOUT running setup,
  // so this must not execute at import time.
  const fixtures = getFixtures()

  // 1. Visit the confirmation link with the freshly minted token. The page
  // calls the API on mount.
  await page.goto('/confirm/' + fixtures.confirm.token)

  // 2. Success Alert appears.
  await expect(page.getByText(/your email is confirmed/i)).toBeVisible()

  // 3. The page auto-redirects to /login after ~1.5s. Wait for that landing.
  await expect(page).toHaveURL(/\/login/)

  // 4. The user is now confirmed, so logging in succeeds and lands on /clients.
  await page.getByLabel('Email').fill(fixtures.confirm.email)
  await page.getByLabel('Password').fill(fixtures.confirm.password)
  await page.getByRole('button', { name: /log in/i }).click()

  await expect(page).toHaveURL(/\/clients$/)
})
