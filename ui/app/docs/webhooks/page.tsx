import type { Metadata } from 'next'
import { C, Code } from '../_ui'

export const metadata: Metadata = {
  title: 'Webhooks',
  description: 'Signed delivery to your endpoint, and how to verify the signature.',
  alternates: { canonical: 'https://senebiclabs.com/docs/webhooks' },
}

export default function Page() {
  return (
    <>
        <section>
          <span className="docs-eyebrow">Delivery</span>
          <h1>Webhooks <span className="tag">optional, signed</span></h1>
          <p>
            If you registered a <C>webhook_url</C>, we POST it once when the batch is delivered,
            so you do not have to poll. The body is the same shape as the delivered{' '}
            <C>GET /results</C> response:
          </p>
          <Code>{`POST https://your-app.com/hooks/senebiclabs
Content-Type: application/json
X-Senebiclabs-Signature: sha256=<hex>

{
  "event": "results.delivered",
  "project_id": "...",
  "company": "Your Company",
  "report": { ... },
  "items": [ ... ]
}`}</Code>

          <h3>Verify the signature</h3>
          <p>
            Every webhook carries an <C>X-Senebiclabs-Signature</C> header. It is an
            HMAC-SHA256 of the exact request body, keyed with your <C>webhook_secret</C>.
            Recompute it and compare in constant time before you trust the payload. This proves
            the request came from us and was not altered in transit.
          </p>
          <div className="docs-callout">
            <p>
              Compute over the <b>raw request bytes</b>, before any JSON parsing. Parsing and
              re-serialising can change the bytes and break the check.
            </p>
          </div>
          <Code>{`import hmac, hashlib

def verify(raw_body: bytes, header: str, secret: str) -> bool:
    expected = "sha256=" + hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header or "")

# FastAPI example
@app.post("/hooks/senebiclabs")
async def hook(request: Request):
    raw = await request.body()
    sig = request.headers.get("X-Senebiclabs-Signature", "")
    if not verify(raw, sig, WEBHOOK_SECRET):
        raise HTTPException(status_code=401)
    payload = json.loads(raw)   # trusted from here
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
