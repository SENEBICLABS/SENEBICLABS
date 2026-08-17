import { NextRequest, NextResponse } from 'next/server'

const FASTAPI = process.env.FASTAPI_URL ?? 'http://localhost:8000'

export async function POST(req: NextRequest) {
  const key = req.headers.get('x-admin-key') ?? ''
  try {
    const body = await req.json()
    const res = await fetch(`${FASTAPI}/api/v1/project/admin/assign-clinician`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Admin-Key': key },
      body: JSON.stringify(body),
    })
    const data = await res.json()
    if (!res.ok) {
      const detail = data.detail
      const message = typeof detail === 'string' ? detail : 'Could not assign the clinician.'
      return NextResponse.json({ ok: false, message }, { status: res.status })
    }
    return NextResponse.json(data)
  } catch {
    return NextResponse.json({ ok: false, message: 'Unable to reach the server.' }, { status: 503 })
  }
}
