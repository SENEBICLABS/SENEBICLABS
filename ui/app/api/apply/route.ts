import { NextRequest, NextResponse } from 'next/server'
import { insert, notify, configured } from '@/lib/record'

// A clinician offering to join the panel.
//
// This used to post to an API endpoint that no longer exists, into a table
// (expert_applications) that nothing reads and whose NOT NULL columns this form
// never filled. Meanwhile the clinician platform has a working intake writing to
// access_requests, which is the queue an operator actually reviews.
//
// So this writes to access_requests too. One queue, one place to look, and a
// clinician who lands on the marketing site rather than the platform is not lost.
export async function POST(req: NextRequest) {
  let body: Record<string, unknown>
  try {
    body = await req.json()
  } catch {
    return NextResponse.json({ ok: false, message: 'Invalid request.' }, { status: 400 })
  }

  const text = (v: unknown) => (typeof v === 'string' ? v.trim() : '')
  const full_name = text(body.name) || text(body.full_name)
  const email = text(body.email).toLowerCase()

  if (!full_name || !email.includes('@')) {
    return NextResponse.json(
      { ok: false, message: 'Please give your name and a valid email.' },
      { status: 422 }
    )
  }
  if (!configured()) {
    return NextResponse.json(
      { ok: false, message: 'We could not record that. Please email support@senebiclabs.com.' },
      { status: 503 }
    )
  }

  try {
    await insert('access_requests', {
      full_name,
      email,
      specialty: text(body.specialty) || null,
      country: text(body.country) || null,
      linkedin_url: text(body.linkedin_url) || null,
    })
  } catch (err) {
    // Already asked, or already a clinician. Say the same thing either way: whether
    // a given address is known is not something a stranger should be able to probe.
    if (String(err).includes('duplicate') || String(err).includes('23505')) {
      return NextResponse.json({ ok: true, message: 'Thank you. We review every application and will be in touch.' })
    }
    console.error('[apply] insert failed', err)
    return NextResponse.json(
      { ok: false, message: 'We could not record that. Please email support@senebiclabs.com.' },
      { status: 503 }
    )
  }

  await notify(`New clinician request — ${full_name}`, {
    Name: full_name,
    Email: email,
    Specialty: body.specialty,
    Country: body.country,
    Note: body.note,
    Source: 'senebiclabs.com/experts',
  })

  return NextResponse.json({
    ok: true,
    message: 'Thank you. We review every application and will be in touch.',
  })
}
