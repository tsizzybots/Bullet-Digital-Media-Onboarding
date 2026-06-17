import { AuthenticatedShell } from '@/components/authenticated-shell'
import { HealthStatus } from '@/components/health-status'
import { UnlinkedTranscripts } from '@/components/unlinked-transcripts'

export default function TranscriptsPage() {
  return (
    <AuthenticatedShell>
      <main className="p-8">
        <div className="mb-4 flex items-center justify-between">
          <h1 className="text-2xl font-bold">Unlinked transcripts</h1>
          <HealthStatus />
        </div>
        <p className="mb-6 text-sm text-muted-foreground">
          Sales-call transcripts that could not be matched to a client automatically. Attach
          each to the right client by hand. Updates automatically.
        </p>
        <UnlinkedTranscripts />
      </main>
    </AuthenticatedShell>
  )
}
