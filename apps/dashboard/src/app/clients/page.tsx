import { AuthenticatedShell } from '@/components/authenticated-shell'
import { ClientsTable } from '@/components/clients-table'
import { HealthStatus } from '@/components/health-status'

export default function ClientsPage() {
  return (
    <AuthenticatedShell>
      <main className="p-8">
        <div className="mb-4 flex items-center justify-between">
          <h1 className="text-2xl font-bold">Clients</h1>
          {/* Live, typed /healthz poll (S1-17) kept as the chrome's API-status dot. */}
          <HealthStatus />
        </div>
        <p className="mb-6 text-sm text-muted-foreground">
          Every client and where they are in onboarding. Updates automatically.
        </p>
        <ClientsTable />
      </main>
    </AuthenticatedShell>
  )
}
