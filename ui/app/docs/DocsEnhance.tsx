'use client'

import { useEffect } from 'react'

// Progressive enhancement for the (server-rendered) docs page: code blocks with
// a language label and a copy button, linkable headings, and active-section
// highlighting in the sidebar. Renders nothing.

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

    // ── Active highlighting: the sidebar follows the section in view ─────────
    const sideLinks = Array.from(document.querySelectorAll<HTMLAnchorElement>('.docs-nav a[href^="#"]'))
    const sideById = new Map(sideLinks.map(a => [a.getAttribute('href')!.slice(1), a]))

    const sectionObserver = new IntersectionObserver(
      entries => entries.forEach(e => {
        if (!e.isIntersecting) return
        sideLinks.forEach(l => l.classList.remove('active'))
        sideById.get(e.target.id)?.classList.add('active')
      }),
      { rootMargin: '-12% 0px -78% 0px', threshold: 0 }
    )
    document.querySelectorAll<HTMLElement>('section[id]').forEach(s => sectionObserver.observe(s))

    return () => sectionObserver.disconnect()
  }, [])

  return null
}
