'use client'

import { useParams } from 'next/navigation'

import { AuthenticatedShell } from '@/components/authenticated-shell'
import { ClientDetail } from '@/components/client-detail'

export default function ClientDetailPage() {
  // useParams (not the async params prop) per the confirm/[token] precedent -
  // avoids Next 15 Promise-params friction in a client component.
  const { id } = useParams<{ id: string }>()

  return (
    <AuthenticatedShell>
      <main className="p-8">
        <ClientDetail id={id} />
      </main>
    </AuthenticatedShell>
  )
}
