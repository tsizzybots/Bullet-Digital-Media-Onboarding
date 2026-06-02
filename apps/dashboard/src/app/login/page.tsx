'use client'

import { Suspense, useState, type FormEvent } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'

import { Alert } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { login, resendConfirmation } from '@/lib/auth'

/** Only allow same-origin relative redirects; never `//host` or absolute URLs. */
function safeNext(next: string | null): string {
  if (next && next.startsWith('/') && !next.startsWith('//')) {
    return next
  }
  return '/clients'
}

function LoginForm() {
  const router = useRouter()
  const searchParams = useSearchParams()

  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Whether the last failure was a 403 (unconfirmed email) -> offer resend.
  const [needsConfirmation, setNeedsConfirmation] = useState(false)
  const [resendSent, setResendSent] = useState(false)

  function clearFeedback() {
    setError(null)
    setNeedsConfirmation(false)
    setResendSent(false)
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    clearFeedback()
    setSubmitting(true)

    try {
      const result = await login(email, password)

      if (result.ok) {
        router.push(safeNext(searchParams.get('next')))
        return
      }

      setError(result.message)
      if (result.status === 403) {
        setNeedsConfirmation(true)
      }
    } finally {
      setSubmitting(false)
    }
  }

  async function handleResend() {
    await resendConfirmation(email)
    setResendSent(true)
  }

  return (
    <div className="w-full max-w-sm space-y-6">
      <h1 className="text-2xl font-bold text-center">Bullet Digital Media</h1>

      <form onSubmit={handleSubmit} className="space-y-4">
        <div className="space-y-2">
          <Label htmlFor="email">Email</Label>
          <Input
            id="email"
            name="email"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(event) => {
              setEmail(event.target.value)
              clearFeedback()
            }}
          />
        </div>

        <div className="space-y-2">
          <Label htmlFor="password">Password</Label>
          <Input
            id="password"
            name="password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(event) => {
              setPassword(event.target.value)
              clearFeedback()
            }}
          />
        </div>

        {error ? <Alert variant="error">{error}</Alert> : null}

        <Button type="submit" variant="primary" disabled={submitting} className="w-full">
          {submitting ? 'Logging in…' : 'Log in'}
        </Button>
      </form>

      {needsConfirmation ? (
        <div className="space-y-3">
          <Button
            type="button"
            variant="secondary"
            disabled={submitting}
            className="w-full"
            onClick={handleResend}
          >
            Resend confirmation email
          </Button>
          {resendSent ? (
            <Alert variant="info">
              If your account needs confirming, a new link is on its way to your inbox.
            </Alert>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}

export default function LoginPage() {
  return (
    <div className="min-h-screen flex items-center justify-center">
      <Suspense
        fallback={
          <div className="w-full max-w-sm space-y-6">
            <h1 className="text-2xl font-bold text-center">Bullet Digital Media</h1>
          </div>
        }
      >
        <LoginForm />
      </Suspense>
    </div>
  )
}
