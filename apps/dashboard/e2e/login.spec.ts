import { expect, test } from '@playwright/test'

import { getFixtures } from './fixtures'

test('seeded user logs in, sees /clients, logs out, stale cookie rejected', async ({
  page,
  context,
}) => {
  // Read fixtures lazily at runtime: globalSetup writes .fixtures.json before
  // the run, but `playwright test --list` collects specs WITHOUT running setup,
  // so this must not execute at import time.
  const fixtures = getFixtures()

  // 1. Log in with the pre-confirmed seed user.
  await page.goto('/login')
  await page.getByLabel('Email').fill(fixtures.login.email)
  await page.getByLabel('Password').fill(fixtures.login.password)
  await page.getByRole('button', { name: /log in/i }).click()

  // 2. Land on /clients with the authenticated shell rendered.
  await expect(page).toHaveURL(/\/clients$/)
  await expect(page.getByRole('button', { name: /log out/i })).toBeVisible()
  await expect(page.getByText(/client list/i)).toBeVisible()

  // 3. Capture the session cookie BEFORE logging out (logout clears it).
  const cookies = await context.cookies()
  const session = cookies.find((c) => c.name === 'session')
  expect(session, 'expected a `session` cookie after login').toBeTruthy()

  // 4. Log out -> redirected back to /login.
  await page.getByRole('button', { name: /log out/i }).click()
  await expect(page).toHaveURL(/\/login/)

  // 5. Stale-cookie rejection: re-inject the now server-side-invalidated
  // session cookie. The middleware lets the request through (a cookie is
  // present), but the authenticated shell's /me call 401s, and the client-side
  // guard bounces back to /login.
  await context.addCookies([
    {
      name: 'session',
      value: session!.value,
      domain: session!.domain,
      path: '/',
      httpOnly: true,
      secure: true,
      sameSite: 'Lax',
    },
  ])
  await page.goto('/clients')
  await expect(page).toHaveURL(/\/login/)
})
