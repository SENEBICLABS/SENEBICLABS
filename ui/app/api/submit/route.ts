import { NextRequest, NextResponse } from 'next/server'
import { insert, notify, configured } from '@/lib/record'

// A company asking about an evaluation. This is the most valuable thing the site
// receives, so it is written straight to Supabase rather than relayed through the
// API: an enquiry must survive the backend being down.
export async function POST(req: NextRequest) {
  let body: Record<string, string>
  try {
    body = await req.json()
  } catch {
    return NextResponse.json({ ok: false, message: 'Invalid request.' }, { status: 400 })
  }

  const name = (body.name ?? '').trim()
  const email = (body.email ?? '').trim().toLowerCase()
  const company = (body.company ?? '').trim()
  const description = (body.description ?? '').trim()

  if (!name || !email || !company || !description) {
    return NextResponse.json(
      { ok: false, message: 'Please fill in your name, work email, company and what you need.' },
      { status: 422 }
    )
  }
  if (!configured()) {
    console.error('[submit] Supabase is not configured; the enquiry was not stored')
    return NextResponse.json(
      { ok: false, message: 'We could not record that. Please email support@senebiclabs.com.' },
      { status: 503 }
    )
  }

  try {
    await insert('project_submissions', {
      name,
      email,
      company,
      description,
      task_type: (body.task_type ?? '').trim() || null,
      status: 'new',
    })
  } catch (err) {
    console.error('[submit] insert failed', err)
    return NextResponse.json(
      { ok: false, message: 'We could not record that. Please email support@senebiclabs.com.' },
      { status: 503 }
    )
  }

  await notify(`New project — ${company}`, {
    Name: name,
    Email: email,
    Company: company,
    Interested_in: body.task_type,
    Message: description,
  })

  return NextResponse.json({
    ok: true,
    message: 'Submission received. We will be in touch within one business day to scope your pilot.',
  })
}
