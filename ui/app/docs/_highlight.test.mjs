// Tests for the reference's language detection and highlighter.
//
//   npm run test:docs
//
// No test framework: node:test is built in, and the module under test is pure.
// The script compiles _highlight.ts first, so this imports the same code the
// site ships rather than a copy that can drift.

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

import { detectLang, highlight, esc } from '../../.docs-test-build/_highlight.js'

const HERE = dirname(fileURLToPath(import.meta.url))

// ── Every code block the reference actually publishes ───────────────────────
// Read out of the page sources, so a new example that lands in the wrong bucket
// fails here instead of shipping as an unlabelled block.
function publishedBlocks() {
  const out = []
  const walk = dir => {
    for (const e of readdirSync(dir, { withFileTypes: true })) {
      const p = join(dir, e.name)
      if (e.isDirectory()) walk(p)
      else if (e.name === 'page.tsx') {
        const src = readFileSync(p, 'utf8')
        const re = /<Code>\{`([\s\S]*?)`\}<\/Code>/g
        let m
        while ((m = re.exec(src))) {
          const body = m[1]
          // A block interpolating a value is not a literal; skip it rather than
          // test a string the page never renders.
          if (body.includes('${')) continue
          out.push({ page: p.slice(p.indexOf('app/docs')), code: body })
        }
      }
    }
  }
  walk(HERE)
  return out
}

const BLOCKS = publishedBlocks()

test('the page sources were parsed at all', () => {
  assert.ok(BLOCKS.length >= 20, `found only ${BLOCKS.length} code blocks`)
})

test('no published block falls through to unlabelled, except a bare URL', () => {
  const plain = BLOCKS.filter(b => detectLang(b.code) === 'text')
  for (const b of plain) {
    // A single bare URL is legitimately plain: there is nothing to highlight.
    assert.match(
      b.code.trim(), /^https?:\/\/\S+$/,
      `${b.page} falls through to 'text':\n  ${b.code.trim().split('\n')[0]}`)
  }
})

test('every published block survives highlighting losslessly', () => {
  const strip = s => s.replace(/<span class="tk-[a-z]+">/g, '').replace(/<\/span>/g, '')
  const unesc = s => s.replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&')
  for (const b of BLOCKS) {
    assert.equal(unesc(strip(highlight(b.code, detectLang(b.code)))), b.code, `in ${b.page}`)
  }
})

test('no published block emits a nested span', () => {
  for (const b of BLOCKS) {
    const html = highlight(b.code, detectLang(b.code))
    assert.doesNotMatch(html, /<span[^>]*>[^<]*<span/, `in ${b.page}`)
  }
})

// ── Detection, case by case ─────────────────────────────────────────────────

test('a shell command is bash even when its body is JSON', () => {
  assert.equal(detectLang(`curl -X POST "$BASE/ingest" \\\n  -d '{ "a": 1 }'`), 'bash')
  assert.equal(detectLang('export BASE=https://api.senebiclabs.com'), 'bash')
})

test('a document opening with a brace or bracket is json', () => {
  assert.equal(detectLang('{\n  "ok": true\n}'), 'json')
  assert.equal(detectLang('[\n  { "a": 1 }\n]'), 'json')
})

test('a json fragment quoted from mid payload is json', () => {
  assert.equal(detectLang('"webhook": { "delivered": false, "status": 502 }'), 'json')
  assert.equal(detectLang('"source": { "manifest_url": "https://b/x.json" }'), 'json')
})

test('a request line is http, with or without a body', () => {
  assert.equal(detectLang('GET $BASE/failures?status=open'), 'http')
  assert.equal(detectLang('POST /benchmark/run\n\n{ "model_version": "v2.3" }'), 'http')
  assert.equal(detectLang('POST https://your-app.com/hooks/senebiclabs'), 'http')
  assert.equal(detectLang('DELETE $BASE/keys/abc'), 'http')
})

test('a bare header is http', () => {
  assert.equal(detectLang('Authorization: Bearer <YOUR_API_KEY>'), 'http')
})

test('a bare URL is not mistaken for a header and stays plain', () => {
  assert.equal(detectLang('https://api.senebiclabs.com/api/v1/project'), 'text')
})

test('python and javascript still detect', () => {
  assert.equal(detectLang('import requests\n\nrequests.get(url)'), 'python')
  assert.equal(detectLang('const r = await fetch(url)'), 'javascript')
})

// ── Escaping ────────────────────────────────────────────────────────────────

test('markup in an example renders as text, not as markup', () => {
  const html = highlight('Authorization: Bearer <YOUR_API_KEY>', 'http')
  assert.match(html, /&lt;YOUR_API_KEY&gt;/)
  assert.doesNotMatch(html, /<YOUR_API_KEY>/)
})

test('an ampersand is escaped once, not twice', () => {
  assert.equal(esc('a & b'), 'a &amp; b')
  assert.match(highlight('GET /x?a=1&b=2', 'http'), /a=<span class="tk-num">1<\/span>&amp;b=/)
})

test('text passes through escaped but unhighlighted', () => {
  assert.equal(highlight('<b>1</b>', 'text'), '&lt;b&gt;1&lt;/b&gt;')
})

// ── Tokens ──────────────────────────────────────────────────────────────────

test('a quoted string followed by a colon is a key, a value is not', () => {
  const html = highlight('{ "ok": "yes" }', 'json')
  assert.match(html, /<span class="tk-key">"ok"<\/span>/)
  assert.match(html, /<span class="tk-str">"yes"<\/span>/)
})

test('a colon on the next line does not turn the string into a key', () => {
  const html = highlight('[ "a"\n, "b" ]', 'json')
  assert.doesNotMatch(html, /tk-key/)
})

test('an unquoted header name is ranked like a key', () => {
  const html = highlight('Authorization: Bearer <K>\nContent-Type: application/json', 'http')
  assert.match(html, /<span class="tk-key">Authorization<\/span>:/)
  assert.match(html, /<span class="tk-key">Content-Type<\/span>:/)
})

test('a header quoted inside a shell argument stays one string', () => {
  const html = highlight(`curl -H "Content-Type: application/json"`, 'bash')
  assert.match(html, /<span class="tk-str">"Content-Type: application\/json"<\/span>/)
})

test('a bare URL at line start is not read as a header name', () => {
  assert.doesNotMatch(highlight('https://api.senebiclabs.com/x', 'http'), /tk-key/)
})

test('every published block that carries a language yields at least one token', () => {
  for (const b of BLOCKS) {
    const lang = detectLang(b.code)
    if (lang === 'text') continue
    assert.match(highlight(b.code, lang), /class="tk-/, `${b.page} produced no tokens`)
  }
})

test('the request verb is marked, and only at the start of a line', () => {
  assert.match(highlight('GET /x', 'http'), /^<span class="tk-fn">GET<\/span>/)
  assert.doesNotMatch(highlight('{ "m": "POST" }', 'json'), /tk-fn/)
})

test('booleans, numbers, flags and comments are marked', () => {
  assert.match(highlight('{ "a": true }', 'json'), /tk-bool/)
  assert.match(highlight('{ "a": 502 }', 'json'), /tk-num/)
  assert.match(highlight('curl -H "x: y"', 'bash'), /tk-flag/)
  assert.match(highlight('# set the key\ncurl', 'bash'), /tk-com/)
})

test('a number inside a string is not separately marked', () => {
  const html = highlight('{ "k": "batch-2026-08-12" }', 'json')
  assert.doesNotMatch(html, /tk-num/)
})

// ── Inputs that must not throw ──────────────────────────────────────────────

test('pathological input is survived', () => {
  const nasty = ['', '"', '""', "'", '\\', '\n\n\n', '{', '#', '//', '- -- ---',
                 '"'.repeat(500), '<'.repeat(500), 'a'.repeat(5000),
                 '"unterminated', '{ "a": "b\\" }', '\u0000�']
  for (const s of nasty) {
    for (const l of ['bash', 'json', 'http', 'python', 'javascript', 'text']) {
      assert.doesNotThrow(() => highlight(s, l), `highlight(${JSON.stringify(s)}, ${l})`)
    }
    assert.doesNotThrow(() => detectLang(s))
  }
})

test('the shared regexes do not leak lastIndex between calls', () => {
  const a = highlight('{ "a": 1 }', 'json')
  const b = highlight('{ "a": 1 }', 'json')
  assert.equal(a, b)
})

test('no rule uses lookbehind, which is a parse error on older Safari', () => {
  const src = readFileSync(join(HERE, '_highlight.ts'), 'utf8')
  const code = src.split('\n').filter(l => !l.trim().startsWith('//')).join('\n')
  assert.doesNotMatch(code, /\(\?<[=!]/)
})
