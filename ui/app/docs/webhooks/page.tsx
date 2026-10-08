import type { Metadata } from 'next'
import { C, Code } from '../_ui'

export const metadata: Metadata = {
  title: 'Webhooks',
  description: 'Signed delivery to your endpoint, and how to verify the signature.',
  alternates: { canonical: 'https://senebiclabs.com/docs/webhooks' },
  openGraph: { url: 'https://senebiclabs.com/docs/webhooks' },
}

export default function Page() {
  return (
    <>
        <section>
          <span className="docs-kicker">Delivery</span>
          <h1>Webhooks <span className="tag">optional, signed</span></h1>
          <p>
            If you registered a <C>webhook_url</C>, we POST it once when the batch is delivered,
            so you do not have to poll. The body is the same shape as the delivered{' '}
            <C>GET /results</C> response:
          </p>
          <Code>{`POST https://your-app.com/hooks/senebiclabs
Content-Type: application/json
X-Senebiclabs-Signature: sha256=<hex>
X-Senebiclabs-Timestamp: 1760000000
X-Senebiclabs-Event-Id: 6f1c2a9e-...

{
  "event": "results.delivered",
  "project_id": "...",
  "company": "Your Company",
  "report": { ... },
  "items": [ ... ]
}`}</Code>

          <h3>Verify the signature</h3>
          <p>
            Every webhook carries three headers. <C>X-Senebiclabs-Signature</C> is an
            HMAC-SHA256, keyed with your <C>webhook_secret</C>, over the timestamp and the
            request body joined by a dot: <C>{'<timestamp>.<raw body>'}</C>.
            <C>X-Senebiclabs-Timestamp</C> is the Unix second we sent it, and{' '}
            <C>X-Senebiclabs-Event-Id</C> identifies the delivery.
          </p>
          <p>
            Recompute the signature and compare in constant time before you trust the payload,
            and reject anything whose timestamp is older than a few minutes. The signature proves
            the request came from us unaltered; the timestamp is what stops a captured payload
            being replayed against you later. Store the event id and ignore one you have already
            processed, so a redelivery cannot be counted twice.
          </p>
          <div className="docs-callout">
            <p>
              Compute over the <b>raw request bytes</b>, before any JSON parsing. Parsing and
              re-serialising can change the bytes and break the check.
            </p>
          </div>
          <Code>{`import hmac, hashlib, time

TOLERANCE = 300   # seconds

def verify(raw_body: bytes, sig: str, ts: str, secret: str) -> bool:
    if not ts.isdigit() or abs(time.time() - int(ts)) > TOLERANCE:
        return False                      # too old, or from the future: a replay
    signed = ts.encode() + b"." + raw_body
    expected = "sha256=" + hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig or "")

# FastAPI example
@app.post("/hooks/senebiclabs")
async def hook(request: Request):
    raw = await request.body()
    sig = request.headers.get("X-Senebiclabs-Signature", "")
    ts  = request.headers.get("X-Senebiclabs-Timestamp", "")
    if not verify(raw, sig, ts, WEBHOOK_SECRET):
        raise HTTPException(status_code=401)

    event_id = request.headers.get("X-Senebiclabs-Event-Id", "")
    if already_processed(event_id):       # a redelivery; acknowledge and stop
        return {"ok": True}

    payload = json.loads(raw)             # trusted from here
    ...`}</Code>
          <p>
            Return <C>2xx</C> to acknowledge. A <C>5xx</C> or a refused connection is retried up
            to three times (immediately, then after 2s and 6s) — that pattern means your endpoint
            blipped. A <C>4xx</C> is never retried: your service rejected the request itself, and
            repeating it would just deliver the same rejection.
          </p>
          <p>
            The outcome is recorded and returned by <C>GET /results</C> once delivered, so a lost
            webhook is distinguishable from one that was never due:
          </p>
          <Code>{`"webhook": { "delivered": false, "status": 502, "attempts": 3, "at": "..." }`}</Code>
          <p>
            For an outage longer than the retries, or a changed URL, re-send it with{' '}
            <C>POST /webhook/redeliver</C>. <C>GET /results</C> remains the source of truth if
            delivery is critical.
          </p>
        </section>
    </>
  )
}
