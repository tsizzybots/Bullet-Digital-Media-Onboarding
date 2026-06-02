'use client'

import { useRouter } from 'next/navigation'
import { useEffect } from 'react'

import { AppHeader } from '@/components/app-header'
import { useMe } from '@/hooks/use-me'

/**
 * Client-side auth guard + chrome for authenticated pages (S1-18).
 *
 * Wraps protected content. While `/me` is in flight it renders a minimal,
 * non-sensitive placeholder. A 401 surfaces as `isError` (the hook uses
 * `retry: false`), which bounces the user to `/login` - this is what makes a
 * stale or cleared session redirect instead of rendering the dashboard.
 */
export function AuthenticatedShell({ children }: { children: React.ReactNode }) {
  const router = useRouter()
  const { user, isLoading, isError } = useMe()

  useEffect(() => {
    if (isError) {
      router.replace('/login')
    }
  }, [isError, router])

  if (isLoading || isError || !user) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background">
        <p className="text-sm text-muted-foreground">Loading…</p>
      </div>
    )
  }

  return (
    <>
      <AppHeader user={user} />
      {children}
    </>
  )
}
