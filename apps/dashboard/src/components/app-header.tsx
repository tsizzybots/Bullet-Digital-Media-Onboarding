'use client'

import { useQueryClient } from '@tanstack/react-query'
import { useRouter } from 'next/navigation'
import { useState } from 'react'

import { Button } from '@/components/ui/button'
import type { Me } from '@/hooks/use-me'
import { logout } from '@/lib/auth'

/**
 * Authenticated top bar (S1-18).
 *
 * Shows the product wordmark on the left and the signed-in user plus a log-out
 * control on the right. Logging out clears the cached `['me']` identity before
 * redirecting so the auth guard can never flash stale authenticated UI.
 */
export function AppHeader({ user }: { user: Me }) {
  const router = useRouter()
  const queryClient = useQueryClient()
  const [loggingOut, setLoggingOut] = useState(false)

  async function handleLogout() {
    setLoggingOut(true)
    await logout()
    // Drop the cached identity so the guard re-evaluates as unauthenticated
    // and cannot render stale auth state on the way out.
    queryClient.removeQueries({ queryKey: ['me'] })
    router.replace('/login')
  }

  return (
    <header className="flex items-center justify-between border-b border-border px-6 py-4">
      <span className="text-sm font-semibold tracking-tight text-foreground">
        Bullet Digital Media
      </span>
      <div className="flex items-center gap-4">
        <div className="flex flex-col items-end leading-tight">
          <span className="text-sm font-medium text-foreground">
            {user.full_name || user.email}
          </span>
          <span className="text-xs text-muted-foreground">{user.role}</span>
        </div>
        <Button variant="secondary" onClick={handleLogout} disabled={loggingOut}>
          {loggingOut ? 'Logging out…' : 'Log out'}
        </Button>
      </div>
    </header>
  )
}
