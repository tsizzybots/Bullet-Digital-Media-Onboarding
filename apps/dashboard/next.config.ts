import type { NextConfig } from 'next'

const nextConfig: NextConfig = {
  output: 'standalone',
  // @bullet/shared ships TypeScript source (its main/types point at
  // src/index.ts), so Next must transpile it rather than expecting
  // pre-built JS. This keeps the dashboard build free of any separate
  // shared-package build step.
  transpilePackages: ['@bullet/shared'],
}

export default nextConfig
