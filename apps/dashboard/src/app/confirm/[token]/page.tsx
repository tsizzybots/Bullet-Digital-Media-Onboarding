'use client'

import Link from 'next/link'
import { useParams, useRouter } from 'next/navigation'
import { useEffect, useRef, useState } from 'react'

import { Alert } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { confirmEmail } from '@/lib/auth'

/** UI state machine for the confirmation flow. */
type ConfirmState =
  | 'pending'
  | 'confirmed'
  | 'already_confirmed'
  | 'invalid'
  | 'expired'
  | 'error'

/** How long the success state waits before auto-redirecting to /login. */
const REDIRECT_DELAY_MS = 1500

export default function ConfirmPage() {
  // Read the token from the route via the client hook (not the async `params`
  // prop) to avoid Next 15 Promise-params friction in a client component.
  const { token } = useParams<{ token: string }>()
  const router = useRouter()

  const [state, setState] = useState<ConfirmState>('pending')
  const [message, setMessage] = useState<string>('')

  // StrictMode (dev) double-invokes effects; guard so we only fire the
  // confirmation request once. The endpoint is idempotent server-side, but we
  // still avoid the duplicate request.
  const hasRun = useRef(false)

  useEffect(() => {
    if (hasRun.current) return
    hasRun.current = true

    let active = true

    void (async () => {
      const result = await confirmEmail(token)
      if (!active) return

      if (result.ok) {
        setState(result.status)
        return
      }

      setMessage(result.message)
      if (result.status === 400) {
        setState('invalid')
      } else if (result.status === 410) {
        setState('expired')
      } else {
        setState('error')
      }
    })()

    return () => {
      active = false
    }
  }, [token])

  const isSuccess = state === 'confirmed' || state === 'already_confirmed'

  // On success, auto-redirect to /login after a short delay. The manual link is
  // always available for an immediate jump.
  useEffect(() => {
    if (!isSuccess) return

    const timer = setTimeout(() => {
      router.replace('/login')
    }, REDIRECT_DELAY_MS)

    return () => clearTimeout(timer)
  }, [isSuccess, router])

  return (
    <div className="min-h-screen flex items-center justify-center">
      <div className="w-full max-w-sm space-y-6">
        <h1 className="text-2xl font-bold text-center">Bullet Digital Media</h1>

        {state === 'pending' && (
          <p className="text-muted-foreground text-center text-sm">
            Confirming your email&hellip;
          </p>
        )}

        {isSuccess && (
          <div className="space-y-4">
            <Alert variant="success">
              Your email is confirmed. You can now log in.
            </Alert>
            <Link href="/login">
              <Button variant="primary" className="w-full">
                Go to login
              </Button>
            </Link>
          </div>
        )}

        {(state === 'invalid' || state === 'expired' || state === 'error') && (
          <div className="space-y-4">
            <Alert variant="error">{message}</Alert>
            <Link
              href="/login"
              className="block text-center text-sm text-muted-foreground underline underline-offset-4 hover:text-foreground"
            >
              Go to login
            </Link>
          </div>
        )}
      </div>
    </div>
  )
}
