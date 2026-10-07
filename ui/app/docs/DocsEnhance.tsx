'use client'

import { useEffect } from 'react'

// Progressive enhancement for the (server-rendered) docs page: a generated
// "on this page" rail, code blocks with a language label and a copy button,
// linkable headings, and active-section highlighting. Renders nothing.

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
    })

    // ── Headings: ids, a hover anchor, and the source for the rail ───────────
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

    // ── On this page ─────────────────────────────────────────────────────────
    const toc = document.getElementById('docs-toc-nav')
    if (toc && !toc.childElementCount) {
      heads.forEach(h => {
        // Endpoint headings are built from spans: a method badge, the path, and a
        // title. `.tag` holds the title on those, but only a qualifier on others
        // ("Webhooks / optional, signed"), so it cannot be stripped. Drop the
        // method badge, keep the rest, and join across element boundaries so the
        // parts do not run together.
        const clone = h.cloneNode(true) as HTMLElement
        clone.querySelectorAll('.h-anchor, .m').forEach(n => n.remove())
        const label = Array.from(clone.childNodes)
          .map(n => (n.textContent ?? '').trim())
          .filter(Boolean)
          .join(' ')
          .replace(/\s+/g, ' ')
          .trim()
        if (!label) return
        const a = document.createElement('a')
        a.href = `#${h.id}`
        a.textContent = label
        if (h.tagName === 'H3') a.className = 'lvl3'
        toc.appendChild(a)
      })
    }

    // ── Active highlighting: sidebar follows sections, rail follows headings ──
    const sideLinks = Array.from(document.querySelectorAll<HTMLAnchorElement>('.docs-nav a[href^="#"]'))
    const tocLinks = Array.from(document.querySelectorAll<HTMLAnchorElement>('.docs-toc a[href^="#"]'))
    const sideById = new Map(sideLinks.map(a => [a.getAttribute('href')!.slice(1), a]))
    const tocById = new Map(tocLinks.map(a => [a.getAttribute('href')!.slice(1), a]))

    const sectionObserver = new IntersectionObserver(
      entries => entries.forEach(e => {
        if (!e.isIntersecting) return
        sideLinks.forEach(l => l.classList.remove('active'))
        sideById.get(e.target.id)?.classList.add('active')
      }),
      { rootMargin: '-12% 0px -78% 0px', threshold: 0 }
    )
    document.querySelectorAll<HTMLElement>('section[id]').forEach(s => sectionObserver.observe(s))

    const headingObserver = new IntersectionObserver(
      entries => entries.forEach(e => {
        if (!e.isIntersecting) return
        tocLinks.forEach(l => l.classList.remove('active'))
        tocById.get(e.target.id)?.classList.add('active')
      }),
      { rootMargin: '-10% 0px -82% 0px', threshold: 0 }
    )
    heads.forEach(h => headingObserver.observe(h))

    return () => { sectionObserver.disconnect(); headingObserver.disconnect() }
  }, [])

  return null
}
