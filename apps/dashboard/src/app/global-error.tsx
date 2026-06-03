'use client'

// Root global error boundary for the dashboard (S1-20, Slice B).
//
// Next.js renders this in place of the root layout when an uncaught error
// bubbles to the top, so it must supply its own <html>/<body>. We report the
// error to Sentry (a no-op when Sentry is disabled) and offer a recovery
// action. Styled with the dashboard's dark tokens.
import * as Sentry from '@sentry/nextjs'
import { useEffect } from 'react'

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string }
  reset: () => void
}) {
  useEffect(() => {
    Sentry.captureException(error)
  }, [error])

  return (
    <html lang="en" className="dark">
      <body className="bg-background text-foreground">
        <div className="flex min-h-screen flex-col items-center justify-center gap-4 px-6 text-center">
          <h1 className="text-2xl font-semibold">Something went wrong</h1>
          <p className="max-w-md text-sm text-muted-foreground">
            An unexpected error occurred. The team has been notified. You can
            try again, and if the problem persists please refresh the page.
          </p>
          <button
            type="button"
            onClick={() => reset()}
            className="inline-flex items-center justify-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-colors hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-foreground focus-visible:ring-offset-2 focus-visible:ring-offset-background"
          >
            Try again
          </button>
        </div>
      </body>
    </html>
  )
}
