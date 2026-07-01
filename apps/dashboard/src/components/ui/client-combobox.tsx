'use client'

import {
  Combobox,
  ComboboxButton,
  ComboboxInput,
  ComboboxOption,
  ComboboxOptions,
} from '@headlessui/react'
import { Check, ChevronsUpDown } from 'lucide-react'
import { useState } from 'react'

import { cn } from '@/lib/utils'

/**
 * Searchable client picker (S1-27a) for the manual transcript-attach action.
 *
 * Headless UI's accessible Combobox styled with Tailwind to match `ui/input`.
 * Filters by business name / contact name / email (substring, case-insensitive)
 * so a team member can find the right client by any of them when there are many.
 * Value is the client id; the parent owns selection state.
 */
export interface ClientOption {
  id: string
  business_name: string | null
  contact_name: string | null
  email: string
}

function clientLabel(client: ClientOption): string {
  return client.business_name || client.contact_name || client.email
}

export function ClientCombobox({
  clients,
  value,
  onChange,
  disabled = false,
  placeholder = 'Search client…',
  inputId,
}: {
  clients: ClientOption[]
  value: string | null
  onChange: (clientId: string | null) => void
  disabled?: boolean
  placeholder?: string
  inputId?: string
}) {
  const [query, setQuery] = useState('')
  const selected = clients.find((client) => client.id === value) ?? null

  const needle = query.trim().toLowerCase()
  const filtered =
    needle === ''
      ? clients
      : clients.filter((client) =>
          [client.business_name, client.contact_name, client.email].some((field) =>
            field?.toLowerCase().includes(needle),
          ),
        )

  return (
    <Combobox
      value={selected}
      onChange={(client: ClientOption | null) => onChange(client?.id ?? null)}
      onClose={() => setQuery('')}
      disabled={disabled}
      immediate
    >
      <div className="relative">
        <ComboboxInput
          id={inputId}
          aria-label="Client"
          autoComplete="off"
          displayValue={(client: ClientOption | null) => (client ? clientLabel(client) : '')}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={placeholder}
          className={cn(
            'flex h-10 w-full rounded-md border border-border bg-background px-3 py-2 pr-9 text-sm text-foreground placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-foreground focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:cursor-not-allowed disabled:opacity-50',
          )}
        />
        <ComboboxButton className="absolute inset-y-0 right-0 flex items-center pr-2">
          <ChevronsUpDown className="h-4 w-4 text-muted-foreground" aria-hidden />
        </ComboboxButton>

        <ComboboxOptions
          // `anchor` portals the panel to the body and floats it via floating-ui,
          // so it escapes the table's `overflow-x-auto` clipping (which otherwise
          // cut the dropdown off + forced an inner table scrollbar). Width is
          // matched to the input via the `--input-width` var Headless UI exposes.
          anchor="bottom start"
          className="z-20 max-h-60 w-[var(--input-width)] overflow-auto rounded-md border border-border bg-background py-1 text-sm shadow-lg [--anchor-gap:4px] focus:outline-none"
        >

          {filtered.length === 0 ? (
            <div className="px-3 py-2 text-muted-foreground">No clients match</div>
          ) : (
            filtered.map((client) => (
              <ComboboxOption
                key={client.id}
                value={client}
                className="group flex cursor-pointer items-center justify-between gap-2 px-3 py-2 data-[focus]:bg-muted"
              >
                <span className="flex min-w-0 flex-col">
                  <span className="truncate text-foreground">{clientLabel(client)}</span>
                  <span className="truncate text-xs text-muted-foreground">{client.email}</span>
                </span>
                <Check
                  className="h-4 w-4 shrink-0 text-foreground opacity-0 group-data-[selected]:opacity-100"
                  aria-hidden
                />
              </ComboboxOption>
            ))
          )}
        </ComboboxOptions>
      </div>
    </Combobox>
  )
}
