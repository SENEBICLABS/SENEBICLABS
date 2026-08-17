import { NextRequest, NextResponse } from 'next/server'

const FASTAPI = process.env.FASTAPI_URL ?? 'http://localhost:8000'

// Proxy a single multipart image upload to the backend, which stores it de-identified
// under the client's private prefix and creates the review task.
export async function POST(req: NextRequest) {
  try {
    const form = await req.formData()
    const res = await fetch(`${FASTAPI}/api/v1/project/portal/upload-image`, {
      method: 'POST',
      body: form,
    })
    const data = await res.json()
    if (!res.ok) {
      const detail = data.detail
      const message = typeof detail === 'string' ? detail : (data.message ?? 'Upload failed.')
      return NextResponse.json({ ok: false, message }, { status: res.status })
    }
    return NextResponse.json(data)
  } catch {
    return NextResponse.json(
      { ok: false, message: 'Unable to reach the server. Please try again shortly.' },
      { status: 503 },
    )
  }
}
