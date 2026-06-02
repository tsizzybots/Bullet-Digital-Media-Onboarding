import { cva, type VariantProps } from 'class-variance-authority'
import * as React from 'react'

import { cn } from '@/lib/utils'

const alertVariants = cva('rounded-md border px-4 py-3 text-sm', {
  variants: {
    variant: {
      error: 'border-red-900/50 bg-red-950/40 text-red-200',
      success: 'border-green-900/50 bg-green-950/40 text-green-200',
      info: 'border-border bg-muted text-muted-foreground',
    },
  },
  defaultVariants: {
    variant: 'info',
  },
})

export interface AlertProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof alertVariants> {}

const Alert = React.forwardRef<HTMLDivElement, AlertProps>(
  ({ className, variant = 'info', ...props }, ref) => {
    return (
      <div
        ref={ref}
        role={variant === 'error' ? 'alert' : undefined}
        className={cn(alertVariants({ variant }), className)}
        {...props}
      />
    )
  },
)
Alert.displayName = 'Alert'

export { Alert, alertVariants }
