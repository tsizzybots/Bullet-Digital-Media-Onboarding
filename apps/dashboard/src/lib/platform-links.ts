// Platform deep-links for the client detail page (S1-32).
//
// Single source of truth mapping each `clients.*_id` column to a display
// label and, where the platform has a stable public URL pattern, an href
// builder. Platforms whose URLs need tenant context we do not store yet
// (HubSpot portal id, GHL agency domain, Xero org, Timely account, Meta
// business id, the Sheets row, Slack thread) render as id-only "connected"
// chips - the card explicitly wants placeholders for platforms not yet
// integrated. Upgrade a platform here (add `url`) as each integration lands.

import type { components } from '@bullet/shared'

type ClientDetail = components['schemas']['ClientDetailResponse']

// Only the nullable-string columns qualify as platform-id keys - this bars
// accidentally pointing a chip at `actions`, `email`, or another non-id field.
type PlatformIdKey = {
  [K in keyof ClientDetail]: null extends ClientDetail[K]
    ? ClientDetail[K] extends string | null
      ? K
      : never
    : never
}[keyof ClientDetail]

export interface PlatformLink {
  /** Field on ClientDetailResponse holding this platform's external id. */
  key: PlatformIdKey
  label: string
  /** Builds the deep-link; absent = render a non-link "connected" chip.
   * NOTE: Stripe URLs assume LIVE-mode objects; test-mode ids need a /test/
   * path segment - revisit when the Stripe integration (Sprint 2) lands. */
  url?: (id: string) => string
}

export const PLATFORM_LINKS: PlatformLink[] = [
  {
    key: 'hubspot_contact_id',
    label: 'HubSpot contact',
  },
  {
    key: 'pandadoc_document_id',
    label: 'PandaDoc agreement',
    url: (id) => `https://app.pandadoc.com/a/#/documents/${id}`,
  },
  { key: 'ghl_subaccount_id', label: 'GHL sub-account' },
  { key: 'ghl_contact_id', label: 'GHL contact' },
  {
    key: 'asana_project_id',
    label: 'Asana project',
    url: (id) => `https://app.asana.com/0/${id}`,
  },
  {
    key: 'asana_finance_task_id',
    label: 'Asana finance task',
    url: (id) => `https://app.asana.com/0/0/${id}`,
  },
  {
    key: 'stripe_customer_id',
    label: 'Stripe customer',
    url: (id) => `https://dashboard.stripe.com/customers/${id}`,
  },
  {
    key: 'stripe_subscription_id',
    label: 'Stripe subscription',
    url: (id) => `https://dashboard.stripe.com/subscriptions/${id}`,
  },
  { key: 'xero_contact_id', label: 'Xero contact' },
  { key: 'timely_client_id', label: 'Timely client' },
  { key: 'timely_project_id', label: 'Timely project' },
  { key: 'meta_ad_account_id', label: 'Meta ad account' },
  {
    key: 'drive_folder_id',
    label: 'Drive folder',
    url: (id) => `https://drive.google.com/drive/folders/${id}`,
  },
  { key: 'sheet_row_id', label: 'Sheet row' },
  { key: 'slack_thread_ts', label: 'Slack thread' },
]
