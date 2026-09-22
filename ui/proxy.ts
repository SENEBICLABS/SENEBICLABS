import { NextResponse } from 'next/server'
import type { NextRequest } from 'next/server'

const COOKIE = 'analyse_access'

export function proxy(request: NextRequest) {
  const token = request.cookies.get(COOKIE)?.value
  const valid = process.env.ANALYSE_ACCESS_TOKEN

  if (!valid) return NextResponse.next()
  if (token === valid) return NextResponse.next()

  return NextResponse.redirect(new URL('/fahimasima/request', request.url))
}

export const config = {
  matcher: ['/fahimasima'],
}
