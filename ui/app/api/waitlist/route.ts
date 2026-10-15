import { NextRequest, NextResponse } from 'next/server'
import { insert, notify, configured } from '@/lib/record'

// This route used to answer "You are on the list" whatever happened, including
// when nothing was stored. It now only says so when a row exists.
export async function POST(req: NextRequest) {
  let body: Record<string, string>
  try {
    body = await req.json()
  } catch {
    return NextResponse.json({ ok: false, message: 'Invalid request.' }, { status: 400 })
  }

  const email = (body.email ?? '').trim().toLowerCase()
  if (!email || !email.includes('@')) {
    return NextResponse.json({ ok: false, message: 'Please enter a valid email.' }, { status: 422 })
  }

  // `type` is NOT NULL and constrained to these four. The pages that use this
  // form always send one; anything else is a caller bug and should say so rather
  // than be quietly filed under a category nobody chose.
  const TYPES = ['patient', 'contributor', 'researcher', 'expert']
  const type = (body.type ?? '').trim()
  if (!TYPES.includes(type)) {
    return NextResponse.json(
      { ok: false, message: 'Please tell us which list to add you to.' },
      { status: 422 }
    )
  }
  if (!configured()) {
    return NextResponse.json(
      { ok: false, message: 'We could not add you just now. Please email support@senebiclabs.com.' },
      { status: 503 }
    )
  }

  try {
    await insert('waitlist', {
      email,
      type,
      location_text: (body.location_text ?? '').trim() || null,
    })
  } catch (err) {
    // A repeat sign-up is not a failure for the person in front of us.
    if (String(err).includes('duplicate') || String(err).includes('23505')) {
      return NextResponse.json({ ok: true, message: 'You are already on the list.' })
    }
    console.error('[waitlist] insert failed', err)
    return NextResponse.json(
      { ok: false, message: 'We could not add you just now. Please email support@senebiclabs.com.' },
      { status: 503 }
    )
  }

  await notify('New waitlist sign-up', { Email: email, Type: body.type, Location: body.location_text })
  return NextResponse.json({ ok: true, message: 'You are on the list.' })
}
