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
      document.removeEventListener('keydown', onKey)
      input?.removeEventListener('input', onInput)
      overlay?.removeEventListener('click', onOverlayClick)
      list?.removeEventListener('click', onListClick)
      triggers.forEach(b => b.removeEventListener('click', open))
    }
  }, [])

  return null
}
