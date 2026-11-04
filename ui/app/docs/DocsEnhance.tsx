'use client'

import { useEffect } from 'react'
import { usePathname } from 'next/navigation'
import { PAGES, href } from './_nav'
import { detectLang, highlight } from './_highlight'

// Progressive enhancement for the (server-rendered) reference: code blocks with
// a language label and a copy button, linkable headings, and search. Renders
// nothing. The sidebar's active state is a route now, so it is handled in React
// rather than here.

const slug = (t: string) =>
  t.toLowerCase().replace(/[^\w\s-]/g, '').trim().replace(/\s+/g, '-').slice(0, 60)

export default function DocsEnhance() {
  // The reference is one layout with eleven routes, so this component mounts
  // once and the pages change underneath it. Keyed on the path, everything below
  // is rebuilt for the page actually on screen: without it the contents rail
  // kept listing the previous page's headings and linking to ids that were no
  // longer in the document, and headings on the new page never got their own.
  const pathname = usePathname()

  useEffect(() => {
    // Set by the cleanup, and checked by anything that can still run after it:
    // a font promise and an animation frame both outlive the effect that armed
    // them, and neither should touch a page the reader has already left.
    let disposed = false

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

      // Remembered on the element so syncScrollable below can name the block
      // without re-detecting, and so it survives the later re-runs.
      if (codeEl) codeEl.dataset.lang = detected
    })

    // ── Keyboard access to a code block that scrolls ───────────────────────
    // A block wider than its box is scrollable, and a browser makes a scrollable
    // box a tab stop so it can be panned with the arrow keys. Left alone that is
    // an unnamed stop with the user agent's own near-black focus ring; naming it
    // and styling the ring in docs.css makes it usable.
    //
    // Whether a block overflows is a layout question, and layout is not settled
    // when this effect first runs: the reference loads Instrument Sans with
    // `display: swap`, so the first measurement uses fallback metrics and a block
    // that will overflow once the real face arrives does not look as though it
    // does yet. Measured once, a first-time reader with a cold font cache got the
    // tab stop on one block out of three. So it runs again when the fonts settle,
    // and again on resize, in both directions.
    const syncScrollable = () => {
      if (disposed) return
      document.querySelectorAll<HTMLElement>('.docs-pre code').forEach(el => {
        if (el.scrollWidth > el.clientWidth) {
          const lang = el.dataset.lang
          el.setAttribute('role', 'region')
          el.setAttribute('tabindex', '0')
          el.setAttribute(
            'aria-label',
            `${!lang || lang === 'text' ? 'Code' : lang} example, scrollable`)
        } else {
          el.removeAttribute('role')
          el.removeAttribute('tabindex')
          el.removeAttribute('aria-label')
        }
      })
    }

    // Coalesced to one measurement per frame: each call reads layout, and a
    // resize drag fires continuously.
    let syncFrame = 0
    const scheduleSync = () => {
      if (disposed) return
      window.cancelAnimationFrame(syncFrame)
      syncFrame = window.requestAnimationFrame(syncScrollable)
    }

    syncScrollable()
    // `disposed` guards the callback, because this settles long after a reader
    // who navigated straight on has unmounted the effect that asked.
    document.fonts?.ready.then(syncScrollable).catch(() => {})
    window.addEventListener('resize', scheduleSync)

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

    // ── A link that arrived with a fragment ────────────────────────────────
    // The ids above are assigned after hydration, so by the time one exists the
    // browser has already looked for the target, found nothing and stayed at the
    // top. Nothing retries it, so a pasted or bookmarked link to a section went
    // nowhere at all. Retried here, now that the ids exist, and only while the
    // reader is still at the top so this cannot yank a page they have scrolled.
    // scrollIntoView honours the scroll-margin-top that clears the sticky bar.
    //
    // decodeURIComponent throws URIError on a malformed escape: '#%', '#%zz',
    // '#a%', or any link truncated mid-escape. Thrown here it would reach the
    // error boundary and unmount the whole reference, leaving the reader
    // "This page couldn't load" with no sidebar to navigate away by. A fragment
    // that cannot be decoded simply names no target.
    let hash = ''
    try {
      hash = decodeURIComponent(window.location.hash.slice(1))
    } catch {
      hash = ''
    }
    if (hash && window.scrollY === 0) {
      document.getElementById(hash)?.scrollIntoView()
    }

    // ── On this page: built from the headings above, so it cannot drift ─────
    // Generated from the DOM rather than declared per page, which is what keeps
    // all eleven content pages untouched by this.
    const tocHost = document.querySelector('.docs-toc')
    let tocObserver: IntersectionObserver | null = null

    // Emptied first: this runs again on every route change, and the rail lives in
    // the layout, so whatever the previous page put here is still in the DOM.
    if (tocHost) tocHost.replaceChildren()

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
      disposed = true
      window.cancelAnimationFrame(syncFrame)
      window.removeEventListener('resize', scheduleSync)
      tocObserver?.disconnect()
      document.removeEventListener('keydown', onKey)
      input?.removeEventListener('input', onInput)
      overlay?.removeEventListener('click', onOverlayClick)
      list?.removeEventListener('click', onListClick)
      triggers.forEach(b => b.removeEventListener('click', open))
    }
  }, [pathname])

  return null
}
