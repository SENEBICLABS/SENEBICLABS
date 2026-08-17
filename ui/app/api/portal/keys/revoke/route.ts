import { NextRequest, NextResponse } from 'next/server'

const FASTAPI = process.env.FASTAPI_URL ?? 'http://localhost:8000'

export async function POST(req: NextRequest) {
  const body = await req.json()
  try {
    const res = await fetch(`${FASTAPI}/api/v1/project/portal/keys/revoke`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      cache: 'no-store',
    })
    const data = await res.json()
    return NextResponse.json(data, { status: res.status })
  } catch {
    return NextResponse.json({ ok: false, message: 'Unable to reach the server.' }, { status: 503 })
  }
}
