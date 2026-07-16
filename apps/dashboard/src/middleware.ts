import type { NextRequest } from 'next/server'
import { NextResponse } from 'next/server'

const PUBLIC_PATHS = ['/login', '/confirm', '/api/healthz']

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl

  if (PUBLIC_PATHS.some((p) => pathname.startsWith(p))) {
    return NextResponse.next()
  }

  const sessionCookie = request.cookies.get('session')
  if (!sessionCookie?.value) {
    const loginUrl = new URL('/login', request.url)
    loginUrl.searchParams.set('next', pathname)
    return NextResponse.redirect(loginUrl)
  }

  return NextResponse.next()
}

export const config = {
  // Gate PAGE routes only. Exclude `api` so the middleware never intercepts the
  // dashboard's own API routes - `/api/healthz` and, critically, the
  // `/api/backend/*` same-origin proxy to the backend API (which carries the
  // session cookie and is authenticated by the API itself). Without this
  // exclusion the middleware would redirect the login POST to /login before the
  // rewrite could proxy it, so login could never complete.
  matcher: ['/((?!api|_next/static|_next/image|favicon.ico).*)'],
}
