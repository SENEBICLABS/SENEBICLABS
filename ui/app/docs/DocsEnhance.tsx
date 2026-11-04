'use client'

import { useEffect } from 'react'
import { PAGES, href } from './_nav'

// Progressive enhancement for the (server-rendered) reference: code blocks with
// a language label and a copy button, linkable headings, and search. Renders
// nothing. The sidebar's active state is a route now, so it is handled in React
// rather than here.

const slug = (t: string) =>
  t.toLowerCase().replace(/[^\w\s-]/g, '').trim().replace(/\s+/g, '-').slice(0, 60)

// Good enough to label a block. Wrong guesses are cosmetic.
function detectLang(code: string): string {
  const t = code.trimStart()
  if (/^(curl|export|BASE=|KEY=|\$ )/.test(t)) return 'bash'
  if (/^(from|import|def |@app|async def)/m.test(t)) return 'python'
  if (/^[[{]/.test(t)) return 'json'
  if (/^(const|function|app\.|await |=>)/m.test(t)) return 'javascript'
  return 'text'
}

const esc = (s: string) =>
  s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')

// Tokens are emitted as spans with a class; the colours live in docs.css and are
// graded in white, because the brand has no hue. Ranking the key above the value
// above the aside is what a reader needs from a payload, and weight does that.
//
// The raw text is tokenised first and escaped on the way out, never the other way
// round: escaping first turns every quote into &quot; and the string rule stops
// matching, which is the usual way a hand-rolled highlighter mangles its input.
const RULES: Array<[RegExp, string]> = [
  [/(^|\n)\s*(#|\/\/)[^\n]*/g, 'tk-com'],          // comment to end of line
  [/"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'/g, 'tk-str'],  // quoted string
  [/\b(true|false|null|None|True|False)\b/g, 'tk-bool'],
  [/\b\d+(?:\.\d+)?\b/g, 'tk-num'],
  // No lookbehind: a regex literal using one is a *parse-time* SyntaxError on iOS
  // Safari before 16.4, which would take this whole component down rather than
  // just the highlighting. The leading space is matched and then skipped by the
  // trimStart offset below, which the comment rule already needed.
  [/(?:^|\s)--?[A-Za-z][\w-]*/g, 'tk-flag'],         // -H, --data-raw
]

function highlight(code: string, lang: string): string {
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
    const isKey = h.cls === 'tk-str' && /^\s*:/.test(code.slice(h.end))
    out += `<span class="${isKey ? 'tk-key' : h.cls}">${esc(text)}</span>`
    at = h.end
  }
  return out + esc(code.slice(at))
}

export default function DocsEnhance() {
  useEffect(() => {
    // ── Code blocks: header strip with a language label and the copy button ──
    document.querySelectorAll<HTMLElement>('.docs-pre').forEach(pre => {
      if (pre.querySelector('.pre-head')) return
      const code = pre.querySelector('code')?.textContent ?? ''

      const head = document.createElement('div')
      head.className = 'pre-head'

      const detected = detectLang(code)
      const lang = document.createElement('span')
      lang.className = 'pre-lang'
      lang.textContent = detected === 'text' ? '' : detected

      const btn = document.createElement('button')
      btn.className = 'copy-btn'
      btn.type = 'button'
      btn.textContent = 'Copy'
      btn.setAttribute('aria-label', 'Copy code')
      btn.addEventListener('click', () => {
        navigator.clipboard.writeText(code).then(() => {
          btn.textContent = 'Copied'
          window.setTimeout(() => { btn.textContent = 'Copy' }, 1500)
        })
      })

      head.append(lang, btn)
      pre.prepend(head)

      // Highlighting after the header is in place, so the copy button keeps
      // handing over the original text rather than the marked-up version.
      const codeEl = pre.querySelector('code')
      if (codeEl && !codeEl.dataset.hl) {
        codeEl.innerHTML = highlight(code, detected)
        codeEl.dataset.hl = '1'
      }
    })

    // ── Headings: ids and a hover anchor, so any subsection is linkable ─────
    const main = document.querySelector('.docs-main')
    const heads = Array.from(main?.querySelectorAll<HTMLElement>('h2, h3') ?? [])
    const used = new Set<string>()

    heads.forEach(h => {
      if (!h.id) {
        let id = slug(h.textContent ?? '')
        if (!id) return
        let n = 2
        while (used.has(id)) id = `${slug(h.textContent ?? '')}-${n++}`
        h.id = id
      }
      used.add(h.id)
      if (!h.querySelector('.h-anchor')) {
        const a = document.createElement('a')
        a.className = 'h-anchor'
        a.href = `#${h.id}`
        a.textContent = '#'
        a.setAttribute('aria-label', `Link to ${h.textContent}`)
        h.prepend(a)
      }
    })

    // ── On this page: built from the headings above, so it cannot drift ─────
    // Generated from the DOM rather than declared per page, which is what keeps
    // all eleven content pages untouched by this.
    const tocHost = document.querySelector('.docs-toc')
    let tocObserver: IntersectionObserver | null = null

    if (tocHost && heads.length > 1) {
      const title = document.createElement('div')
      title.className = 'docs-toc-t'
      title.textContent = 'On this page'
      tocHost.append(title)

      const links = new Map<string, HTMLAnchorElement>()
      heads.forEach(h => {
        if (!h.id) return
        const a = document.createElement('a')
        a.href = `#${h.id}`
        if (h.tagName === 'H3') a.className = 'lvl3'
        // The anchor the heading carries is a '#', so read the heading's own text.
        a.textContent = (h.textContent ?? '').replace(/^#/, '').trim()
        tocHost.append(a)
        links.set(h.id, a)
      })

      // Whichever heading is highest on screen wins, so scrolling up marks the
      // section you are entering rather than the one you just left.
      tocObserver = new IntersectionObserver(
        entries => {
          const onScreen = entries.filter(e => e.isIntersecting)
          if (!onScreen.length) return
          const top = onScreen.reduce((a, b) =>
            a.boundingClientRect.top < b.boundingClientRect.top ? a : b)
          links.forEach(l => l.classList.remove('active'))
          links.get((top.target as HTMLElement).id)?.classList.add('active')
        },
        { rootMargin: '-64px 0px -70% 0px', threshold: 0 },
      )
      heads.forEach(h => { if (h.id) tocObserver?.observe(h) })
    }

    // ── Search ───────────────────────────────────────────────────────────────
    // Built from the page itself, so it indexes whatever is published rather than
    // a list someone has to remember to update. Each heading carries the prose
    // that follows it, because people search for a sentence, not a title.
    type Entry = { url: string; title: string; crumb: string; body: string }

    // Every page by name, so search can take you somewhere you are not, plus the
    // headings of the page you are on with the prose under each, because people
    // search for a sentence rather than a title. Full text for other pages would
    // need a build-time index; this is the useful 90% without one.
    const index: Entry[] = [
      ...PAGES.map(p => ({ url: href(p.slug), title: p.label, crumb: p.group ?? 'Page', body: '' })),
      ...heads.map(h => {
        let body = ''
        let n = h.nextElementSibling
        while (n && !/^H[23]$/.test(n.tagName)) {
          body += ' ' + (n.textContent ?? '')
          n = n.nextElementSibling
        }
        return {
          url: `#${h.id}`,
          title: (h.textContent ?? '').replace(/^#/, '').trim(),
          crumb: 'On this page',
          body: body.replace(/\s+/g, ' ').slice(0, 600),
        }
      }),
    ]

    const overlay = document.getElementById('docs-search')
    const input = document.getElementById('docs-search-input') as HTMLInputElement | null
    const list = document.getElementById('docs-search-results')
    let active = 0

    const render = (q: string) => {
      if (!list) return
      const needle = q.trim().toLowerCase()
      const hits = needle
        ? index
            .map(e => {
              const t = e.title.toLowerCase().indexOf(needle)
              const b = e.body.toLowerCase().indexOf(needle)
              if (t < 0 && b < 0) return null
              // A title match beats a body match, and an earlier match beats a later one.
              return { e, score: t >= 0 ? t : 1000 + b }
            })
            .filter((x): x is { e: Entry; score: number } => x !== null)
            .sort((a, b) => a.score - b.score)
            .slice(0, 12)
            .map(x => x.e)
        : index.slice(0, 8)
      active = 0
      list.innerHTML = hits.length
        ? hits
            .map(
              (e, i) =>
                `<a href="${e.url}" class="sr${i === 0 ? ' active' : ''}">` +
                `<span class="sr-t">${e.title.replace(/</g, '&lt;')}</span>` +
                (e.crumb ? `<span class="sr-c">${e.crumb.replace(/</g, '&lt;')}</span>` : '') +
                `</a>`
            )
            .join('')
        : `<div class="sr-empty">Nothing matches that.</div>`
    }

    const close = () => {
      overlay?.classList.remove('open')
      document.body.style.overflow = ''
    }
    const open = () => {
      overlay?.classList.add('open')
      document.body.style.overflow = 'hidden'
      if (input) { input.value = ''; input.focus() }
      render('')
    }

    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        open()
        return
      }
      if (!overlay?.classList.contains('open')) return
      const items = Array.from(list?.querySelectorAll<HTMLAnchorElement>('.sr') ?? [])
      if (e.key === 'Escape') {
        close()
      } else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault()
        if (!items.length) return
        items[active]?.classList.remove('active')
        active = (active + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length
        items[active]?.classList.add('active')
        items[active]?.scrollIntoView({ block: 'nearest' })
      } else if (e.key === 'Enter') {
        e.preventDefault()
        items[active]?.click()
        close()
      }
    }

    const onInput = () => render(input?.value ?? '')
    const onOverlayClick = (ev: MouseEvent) => { if (ev.target === overlay) close() }
    const onListClick = (ev: MouseEvent) => {
      if ((ev.target as HTMLElement).closest('.sr')) close()
    }
    const triggers = Array.from(document.querySelectorAll<HTMLElement>('[data-open-search]'))

    document.addEventListener('keydown', onKey)
    input?.addEventListener('input', onInput)
    overlay?.addEventListener('click', onOverlayClick)
    list?.addEventListener('click', onListClick)
    triggers.forEach(b => b.addEventListener('click', open))

    return () => {
      tocObserver?.disconnect()
      document.removeEventListener('keydown', onKey)
      input?.removeEventListener('input', onInput)
      overlay?.removeEventListener('click', onOverlayClick)
      list?.removeEventListener('click', onListClick)
      triggers.forEach(b => b.removeEventListener('click', open))
    }
  }, [])

  return null
}
