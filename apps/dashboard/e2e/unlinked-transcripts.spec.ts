import { expect, test } from '@playwright/test'

import { getFixtures } from './fixtures'

/**
 * S1-27a: the unlinked-transcripts page lists a parked transcript and lets a
 * team member attach it to a client via the searchable combobox; once attached
 * the row drops off the list (the mutation invalidates the query).
 *
 * The unlinked transcript is provisioned by global-setup
 * (scripts/seed_e2e_transcripts.py); the clients come from seed_e2e_clients.py.
 * The seed is self-resetting (forces the transcript back to unlinked), so this
 * spec is repeatable across runs.
 */
test('unlinked transcript can be attached to a client', async ({ page }) => {
  const fixtures = getFixtures()

  // Log in -> land on /clients.
  await page.goto('/login')
  await page.getByLabel('Email').fill(fixtures.login.email)
  await page.getByLabel('Password').fill(fixtures.login.password)
  await page.getByRole('button', { name: /log in/i }).click()
  await expect(page).toHaveURL(/\/clients$/)

  // Navigate to the transcripts page via the header nav. Generous timeouts: a
  // cold Next dev server (e.g. CI, or a freshly-cleaned .next) compiles the
  // /transcripts route on first hit, which can exceed the default 5s.
  await page.getByRole('link', { name: 'Unlinked transcripts' }).click()
  await expect(page).toHaveURL(/\/transcripts$/, { timeout: 30_000 })

  // The seeded unlinked transcript is listed (recognised by its participant email).
  await expect(page.getByText('e2e-prospect@e2e.example')).toBeVisible({ timeout: 30_000 })

  // Attach is disabled until a client is chosen.
  const attachButton = page.getByRole('button', { name: 'Attach' }).first()
  await expect(attachButton).toBeDisabled()

  // Pick a client in the searchable combobox, then attach.
  const picker = page.getByLabel('Client').first()
  await picker.click()
  await picker.fill('Alpha')
  await page.getByRole('option', { name: /E2E Alpha Gym/ }).click()
  await expect(attachButton).toBeEnabled()
  await attachButton.click()

  // The row drops off; with a single seeded transcript the empty state appears.
  await expect(page.getByText('All transcripts are linked')).toBeVisible()
  await expect(page.getByText('e2e-prospect@e2e.example')).toHaveCount(0)
})
