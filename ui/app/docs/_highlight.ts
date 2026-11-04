// Language detection and syntax highlighting for the reference's code blocks.
//
// Pure, DOM free and exported on its own so it can be tested directly: see
// _highlight.test.mjs, run with `npm run test:docs`. DocsEnhance imports it and
// is the only caller.

export const esc = (s: string) =>
  s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')

// The label a block carries, and now also whether it is highlighted at all: a
// block detected as 'text' is passed through untouched. That makes a wrong guess
// more than cosmetic, which is why the order below is specific before general.
//
// Checked against every block in the reference by _highlight.test.mjs, so a new
// example that lands in the wrong bucket shows up as a failing test rather than
// as an unlabelled block someone notices months later.
export function detectLang(code: string): string {
  const t = code.trimStart()

  // A shell command wins first: its body often contains JSON, and labelling that
  // whole block 'json' would be wrong.
  if (/^(curl|export|BASE=|KEY=|\$ )/.test(t)) return 'bash'
  if (/^(from|import|def |@app|async def)/m.test(t)) return 'python'
  if (/^[[{]/.test(t)) return 'json'
  if (/^(const|function|app\.|await |=>)/m.test(t)) return 'javascript'

  // A request line, with or without a response body under it.
  if (/^(GET|POST|PUT|PATCH|DELETE|HEAD)\s+\S/m.test(t)) return 'http'

  // A bare header. The space after the colon is required, so a bare URL such as
  // `https://api.senebiclabs.com/...` is not mistaken for one and stays plain.
  if (/^[A-Za-z][\w-]*:[ \t]+\S/.test(t)) return 'http'

  // A JSON fragment quoted from the middle of a payload, so it does not open
  // with a brace: `"webhook": { ... }`.
  if (/^[ \t]*"[^"\n]+"[ \t]*:/m.test(t)) return 'json'

  return 'text'
}

// Tokens are emitted as spans with a class; the colours live in docs.css and are
// graded in white, because the brand has no hue. Ranking the key above the value
// above the aside is what a reader needs from a payload, and weight does that.
//
// The raw text is tokenised first and escaped on the way out, never the other way
// round: escaping first turns every quote into &quot; and the string rule stops
// matching, which is the usual way a hand-rolled highlighter mangles its input.
const RULES: Array<[RegExp, string]> = [
  [/^(GET|POST|PUT|PATCH|DELETE|HEAD)\b/gm, 'tk-fn'],      // request line verb
  [/(^|\n)[ \t]*(#|\/\/)[^\n]*/g, 'tk-com'],               // comment to end of line
  [/"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'/g, 'tk-str'],  // quoted string
  // An unquoted header name, so `Authorization: Bearer ...` ranks its name the
  // way a JSON key is ranked. After the string rule, so a header quoted inside a
  // shell argument stays part of the string. Lookahead, not lookbehind: a
  // lookahead is supported everywhere, and it keeps the colon out of the token.
  [/^[A-Za-z][\w-]*(?=:[ \t])/gm, 'tk-key'],
  [/\b(true|false|null|None|True|False)\b/g, 'tk-bool'],
  [/\b\d+(?:\.\d+)?\b/g, 'tk-num'],
  // No lookbehind: a regex literal using one is a *parse-time* SyntaxError on iOS
  // Safari before 16.4, which would take this whole component down rather than
  // just the highlighting. The leading space is matched and then skipped by the
  // trimStart offset below, which the comment rule already needed.
  [/(?:^|\s)--?[A-Za-z][\w-]*/g, 'tk-flag'],               // -H, --data-raw
]

export function highlight(code: string, lang: string): string {
  if (lang === 'text') return esc(code)

  // One pass, left to right, longest-first by rule order. Overlapping matches are
  // impossible because each match consumes its span.
  type Hit = { start: number; end: number; cls: string }
  const hits: Hit[] = []
  for (const [re, cls] of RULES) {
    re.lastIndex = 0
    let m: RegExpExecArray | null
    while ((m = re.exec(code))) {
      const start = m.index + (m[0].length - m[0].trimStart().length)
      const end = m.index + m[0].length
      if (hits.some(h => start < h.end && end > h.start)) continue
      hits.push({ start, end, cls })
      if (m[0].length === 0) re.lastIndex++
    }
  }
  hits.sort((a, b) => a.start - b.start)

  let out = '', at = 0
  for (const h of hits) {
    if (h.start < at) continue
    out += esc(code.slice(at, h.start))
    const text = code.slice(h.start, h.end)
    // A quoted string that a colon follows is a key, not a value.
    const isKey = h.cls === 'tk-str' && /^[ \t]*:/.test(code.slice(h.end))
    out += `<span class="${isKey ? 'tk-key' : h.cls}">${esc(text)}</span>`
    at = h.end
  }
  return out + esc(code.slice(at))
}
