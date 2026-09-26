import { NextRequest, NextResponse } from 'next/server'

const FASTAPI = process.env.FASTAPI_URL ?? 'http://localhost:8000'

/** Cases where clinicians disagreed, held out of the results until a senior reviewer decides. */
export async function GET(req: NextRequest) {
  const key = req.headers.get('x-admin-key') ?? ''
  const project = req.nextUrl.searchParams.get('project') ?? ''
  try {
    const res = await fetch(
      `${FASTAPI}/api/v1/project/admin/adjudication/${encodeURIComponent(project)}`,
      { headers: { 'X-Admin-Key': key }, cache: 'no-store' }
    )
    const data = await res.json()
    if (!res.ok) {
      const detail = data.detail
      return NextResponse.json(
        { ok: false, message: typeof detail === 'string' ? detail : 'Could not load the queue.' },
        { status: res.status }
      )
    }
    return NextResponse.json(data)
  } catch {
    return NextResponse.json({ ok: false, message: 'Unable to reach the server.' }, { status: 503 })
  }
}
