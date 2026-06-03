'use client'

// Unlinked Sentry verification page (S1-20, Slice B).
//
// Not referenced from any nav. Visiting /debug/sentry and clicking the button
// flips state so the component THROWS on the next render. That uncaught render
// error propagates to src/app/global-error.tsx, which calls
// Sentry.captureException — proving the global-error -> Sentry path end to end
// and (with source maps uploaded) producing a readable stack trace.
import { useState } from 'react'

import { Button } from '@/components/ui/button'

export default function SentryDebugPage() {
  const [boom, setBoom] = useState(false)

  if (boom) {
    throw new Error('Sentry source-map test — dashboard runtime error')
  }

  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-4 bg-background px-6 text-center text-foreground">
      <h1 className="text-2xl font-semibold">Sentry debug</h1>
      <p className="max-w-md text-sm text-muted-foreground">
        This page is for verifying Sentry capture only. The button below throws
        an uncaught render error, which the global error boundary reports to
        Sentry.
      </p>
      <Button onClick={() => setBoom(true)}>Throw test error</Button>
    </main>
  )
}
