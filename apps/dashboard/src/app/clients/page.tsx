import { HealthStatus } from '@/components/health-status'

export default function ClientsPage() {
  return (
    <main className="p-8">
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-2xl font-bold">Clients</h1>
        {/* S1-17 sample: live, typed /healthz poll via TanStack Query. */}
        <HealthStatus />
      </div>
      <p className="text-muted-foreground">Client list -- implemented in S1-31</p>
    </main>
  )
}
