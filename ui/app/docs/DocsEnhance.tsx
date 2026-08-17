'use client'

import { useEffect } from 'react'

// Progressive enhancement for the (server-rendered) docs page: copy buttons on
// code blocks and active-section highlighting in the sidebar. Renders nothing.
export default function DocsEnhance() {
  useEffect(() => {
    // Copy buttons
    const pres = Array.from(document.querySelectorAll<HTMLElement>('.docs-pre'))
    pres.forEach(pre => {
      if (pre.querySelector('.copy-btn')) return
      const btn = document.createElement('button')
      btn.className = 'copy-btn'
      btn.type = 'button'
      btn.textContent = 'Copy'
      btn.setAttribute('aria-label', 'Copy code')
      btn.addEventListener('click', () => {
        const code = pre.querySelector('code')?.textContent ?? ''
        navigator.clipboard.writeText(code).then(() => {
          btn.textContent = 'Copied'
          window.setTimeout(() => { btn.textContent = 'Copy' }, 1500)
        })
      })
      pre.appendChild(btn)
    })

    // Scrollspy — highlight the sidebar link for the section in view
    const links = Array.from(document.querySelectorAll<HTMLAnchorElement>('.docs-nav a[href^="#"]'))
    const byId = new Map<string, HTMLAnchorElement>()
    links.forEach(a => byId.set(a.getAttribute('href')!.slice(1), a))
    const sections = Array.from(document.querySelectorAll<HTMLElement>('section[id]'))

    const observer = new IntersectionObserver(
      entries => {
        entries.forEach(e => {
          if (e.isIntersecting) {
            links.forEach(l => l.classList.remove('active'))
            byId.get(e.target.id)?.classList.add('active')
          }
        })
      },
      { rootMargin: '-12% 0px -78% 0px', threshold: 0 }
    )
    sections.forEach(s => observer.observe(s))

    return () => observer.disconnect()
  }, [])

  return null
}
