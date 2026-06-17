import { cva, type VariantProps } from 'class-variance-authority'
import * as React from 'react'

import { cn } from '@/lib/utils'

/**
 * Small pill used for the step + platform-action-status cells on the clients
 * board (S1-31). Variants reuse the same palette as `Alert` so the page reads
 * as one visual family - `neutral` for step labels, the coloured variants for
 * action health (success / failed / in-flight).
 */
const badgeVariants = cva(
  'inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium',
  {
    variants: {
      variant: {
        neutral: 'border-border bg-muted text-muted-foreground',
        success: 'border-green-900/50 bg-green-950/40 text-green-200',
        warning: 'border-amber-900/50 bg-amber-950/40 text-amber-200',
        danger: 'border-red-900/50 bg-red-950/40 text-red-200',
      },
    },
    defaultVariants: {
      variant: 'neutral',
    },
  },
)

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

const Badge = React.forwardRef<HTMLSpanElement, BadgeProps>(
  ({ className, variant, ...props }, ref) => {
    return (
      <span ref={ref} className={cn(badgeVariants({ variant }), className)} {...props} />
    )
  },
)
Badge.displayName = 'Badge'

export { Badge, badgeVariants }
