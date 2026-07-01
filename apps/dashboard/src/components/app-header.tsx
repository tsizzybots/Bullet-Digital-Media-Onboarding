'use client'

import { useQueryClient } from '@tanstack/react-query'
import Link from 'next/link'
import { usePathname, useRouter } from 'next/navigation'
import { useState } from 'react'

import { Button } from '@/components/ui/button'
import type { Me } from '@/hooks/use-me'
import { logout } from '@/lib/auth'
import { cn } from '@/lib/utils'

const NAV_LINKS = [
  { href: '/clients', label: 'Clients' },
  { href: '/transcripts', label: 'Unlinked transcripts' },
] as const

function NavLinks() {
  const pathname = usePathname()
  return (
    <nav className="flex items-center gap-1" aria-label="Primary">
      {NAV_LINKS.map((link) => {
        const active = pathname === link.href || pathname.startsWith(`${link.href}/`)
        return (
          <Link
            key={link.href}
            href={link.href}
            aria-current={active ? 'page' : undefined}
            className={cn(
              'rounded-md px-3 py-1.5 text-sm font-medium transition-colors',
              active
                ? 'bg-muted text-foreground'
                : 'text-muted-foreground hover:bg-muted hover:text-foreground',
            )}
          >
            {link.label}
          </Link>
        )
      })}
    </nav>
  )
}

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
      <div className="flex items-center gap-6">
        <span className="text-sm font-semibold tracking-tight text-foreground">
          Bullet Digital Media
        </span>
        <NavLinks />
      </div>
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
