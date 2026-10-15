// Durable capture for the inbound forms.
//
// These used to post through the FastAPI backend. That made a lead's survival
// depend on the backend being up, and when it was not, an enquiry vanished with
// a cheerful message. Supabase is a different provider with a different failure
// mode, so the write goes there first and directly. Notifying us is best effort
// and never decides whether the submission is kept.

const URL_ = process.env.SUPABASE_URL
const KEY = process.env.SUPABASE_SERVICE_KEY

export function configured(): boolean {
  return Boolean(URL_ && KEY)
}

/** Insert one row. Throws if the write did not happen, so a caller never claims success. */
export async function insert(table: string, row: Record<string, unknown>): Promise<void> {
  if (!URL_ || !KEY) throw new Error('Supabase is not configured')
  const res = await fetch(`${URL_}/rest/v1/${table}`, {
    method: 'POST',
    headers: {
      apikey: KEY,
      Authorization: `Bearer ${KEY}`,
      'Content-Type': 'application/json',
      Prefer: 'return=minimal',
    },
    body: JSON.stringify(row),
  })
  if (!res.ok) throw new Error(`${table} insert failed: ${res.status} ${await res.text()}`)
}

/** Tell us about it. A failure here is logged and swallowed: the row is already safe. */
export async function notify(subject: string, lines: Record<string, unknown>): Promise<void> {
  const key = process.env.RESEND_API_KEY
  const to = process.env.ADMIN_EMAIL ?? 'support@senebiclabs.com'
  const from = process.env.FROM_EMAIL ?? 'Senebiclabs <noreply@senebiclabs.com>'
  if (!key) return
  const html =
    '<table style="font-family:system-ui,sans-serif;font-size:14px">' +
    Object.entries(lines)
      .filter(([, v]) => v !== undefined && v !== null && v !== '')
      .map(
        ([k, v]) =>
          `<tr><td style="padding:6px 14px 6px 0;color:#666">${k}</td><td>${String(v)
            .replace(/</g, '&lt;')
            .replace(/\n/g, '<br>')}</td></tr>`
      )
      .join('') +
    '</table>'
  try {
    await fetch('https://api.resend.com/emails', {
      method: 'POST',
      headers: { Authorization: `Bearer ${key}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ from, to, reply_to: to, subject, html }),
    })
  } catch (err) {
    console.error('[notify] could not send', subject, err)
  }
}
