import { expect, test } from '@playwright/test'

import { getFixtures } from './fixtures'

/**
 * S1-32: the /clients/[id] detail page.
 *
 * Fixtures from global-setup (scripts/seed_e2e_clients.py): Bravo has a full
 * PRD §7.1 sales summary + an action with inngest_run_id '01E2ESEED0RUN0GHL0BRAVO00';
 * Alpha is deliberately summary-less. Per the card: summary renders for a
 * seeded client, "no summary yet" for one without, and the Inngest run
 * deep-link points at the Inngest UI (href asserted, not navigated - the
 * Inngest dev server is not part of this webServer setup).
 */
test('client detail renders summary, no-summary state, and inngest run link', async ({
  page,
}) => {
  const fixtures = getFixtures()

  await page.goto('/login')
  await page.getByLabel('Email').fill(fixtures.login.email)
  await page.getByLabel('Password').fill(fixtures.login.password)
  await page.getByRole('button', { name: /log in/i }).click()
  await expect(page).toHaveURL(/\/clients$/)

  // Navigate via the list row link - proves the S1-31 -> S1-32 contract.
  // waitForURL is armed BEFORE the click so a 10s-poll re-render of the table
  // between locate and click cannot drop the navigation (flake-proofing).
  await Promise.all([
    page.waitForURL(/\/clients\/[0-9a-f-]{36}$/),
    page.getByRole('link', { name: 'E2E Bravo Fitness' }).click(),
  ])

  // Header metadata + step.
  await expect(page.getByRole('heading', { name: 'E2E Bravo Fitness' })).toBeVisible()
  await expect(page.getByText('Signed', { exact: true })).toBeVisible()

  // Structured §7.1 summary fields render.
  await expect(page.getByText('E2E test gym')).toBeVisible()
  await expect(page.getByText('Goal one')).toBeVisible()
  await expect(page.getByText('1,000 - 2,000 USD / month')).toBeVisible()
  await expect(page.getByText(/E2E quote/)).toBeVisible()

  // Inngest run deep-link points at the Inngest UI for the seeded action.
  const runLink = page.getByTestId('inngest-run-link').first()
  await expect(runLink).toBeVisible()
  await expect(runLink).toHaveAttribute(
    'href',
    'http://localhost:8288/run?runID=01E2ESEED0RUN0GHL0BRAVO00',
  )

  // The summary-less client shows the explicit "no summary yet" state.
  await Promise.all([
    page.waitForURL(/\/clients$/),
    page.getByRole('link', { name: /all clients/i }).click(),
  ])
  await Promise.all([
    page.waitForURL(/\/clients\/[0-9a-f-]{36}$/),
    page.getByRole('link', { name: 'E2E Alpha Gym' }).click(),
  ])
  await expect(page.getByRole('heading', { name: 'E2E Alpha Gym' })).toBeVisible()
  await expect(page.getByText(/no sales summary yet/i)).toBeVisible()
})
