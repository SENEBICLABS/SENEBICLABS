'use client'

import { useEffect, useRef, useState } from 'react'

// "Copy page" and "Open in…", the way hosted references offer it. People paste a
// docs page into an assistant far more often than they read it end to end, and a
// rendered page pastes badly. This hands them the Markdown instead.

// Walk the rendered page rather than keeping a second copy of the content in
// Markdown. Whatever is published is what gets copied, and it cannot drift.
function toMarkdown(root: HTMLElement): string {
  const out: string[] = []

  const inline = (el: Node): string => {
    if (el.nodeType === Node.TEXT_NODE) return el.textContent ?? ''
    if (!(el instanceof HTMLElement)) return ''
    const kids = Array.from(el.childNodes).map(inline).join('')
    switch (el.tagName) {
      case 'CODE': return `\`${kids}\``
      case 'B':
      case 'STRONG': return `**${kids}**`
      case 'I':
      case 'EM': return `*${kids}*`
      case 'A': {
        const href = el.getAttribute('href') ?? ''
        const abs = href.startsWith('/') ? `https://senebiclabs.com${href}` : href
        return abs ? `[${kids}](${abs})` : kids
      }
      case 'BR': return '\n'
      default: return kids
    }
  }

  const block = (el: Element) => {
    if (!(el instanceof HTMLElement)) return
    if (el.classList.contains('docs-actions') || el.classList.contains('docs-pager')) return

    switch (el.tagName) {
      case 'H1': out.push(`# ${inline(el).replace(/^#/, '').trim()}\n`); return
      case 'H2': out.push(`## ${inline(el).replace(/^#/, '').trim()}\n`); return
      case 'H3': out.push(`### ${inline(el).replace(/^#/, '').trim()}\n`); return
      case 'P': {
        const t = inline(el).trim()
        if (t) out.push(`${t}\n`)
        return
      }
      case 'UL':
      case 'OL': {
        const ordered = el.tagName === 'OL'
        Array.from(el.children).forEach((li, i) => {
          const t = inline(li).replace(/\s+/g, ' ').trim()
          if (t) out.push(`${ordered ? `${i + 1}.` : '-'} ${t}`)
        })
        out.push('')
        return
      }
      case 'PRE': {
        const code = el.querySelector('code')?.textContent ?? ''
        const lang = el.querySelector('.pre-lang')?.textContent?.trim() ?? ''
        out.push('```' + lang, code.replace(/\s+$/, ''), '```', '')
        return
      }
      case 'TABLE': {
        const rows = Array.from(el.querySelectorAll('tr'))
        rows.forEach((tr, i) => {
          const cells = Array.from(tr.children).map(c => inline(c).replace(/\s+/g, ' ').trim())
          out.push(`| ${cells.join(' | ')} |`)
          if (i === 0) out.push(`|${cells.map(() => '---').join('|')}|`)
        })
        out.push('')
        return
      }
      case 'SPAN':
        // Kickers sit above the title and read as a breadcrumb in Markdown.
        if (el.classList.contains('docs-kicker')) { out.push(`*${inline(el).trim()}*\n`); return }
        break
    }
    Array.from(el.children).forEach(block)
  }

  Array.from(root.children).forEach(block)
  return out.join('\n').replace(/\n{3,}/g, '\n\n').trim()
}

export default function DocsPageActions() {
  const [open, setOpen] = useState(false)
  const [label, setLabel] = useState('Copy page')
  const box = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const away = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('click', away)
    return () => document.removeEventListener('click', away)
  }, [])

  const markdown = () => {
    const main = document.querySelector('.docs-main')
    return main ? toMarkdown(main as HTMLElement) : ''
  }

  const copy = () => {
    navigator.clipboard.writeText(markdown()).then(() => {
      setLabel('Copied')
      window.setTimeout(() => setLabel('Copy page'), 1600)
    })
    setOpen(false)
  }

  const viewMarkdown = () => {
    const blob = new Blob([markdown()], { type: 'text/plain;charset=utf-8' })
    window.open(URL.createObjectURL(blob), '_blank', 'noopener')
    setOpen(false)
  }

  const askAbout = (base: string) => {
    const url = window.location.href
    const q = `Read ${url} and help me with it.`
    window.open(`${base}${encodeURIComponent(q)}`, '_blank', 'noopener')
    setOpen(false)
  }

  return (
    <div className="docs-actions" ref={box}>
      <button type="button" className="da-main" onClick={copy}>
        <svg width="13" height="13" viewBox="0 0 16 16" fill="none" aria-hidden>
          <rect x="5.4" y="5.4" width="8.2" height="8.2" rx="1.8" stroke="currentColor" strokeWidth="1.3" />
          <path d="M10.6 5.4V4.2A1.8 1.8 0 0 0 8.8 2.4H4.2a1.8 1.8 0 0 0-1.8 1.8v4.6a1.8 1.8 0 0 0 1.8 1.8h1.2"
                stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
        </svg>
        {label}
      </button>
      <button
        type="button"
        className="da-more"
        aria-label="More ways to use this page"
        aria-expanded={open}
        onClick={() => setOpen(v => !v)}
      >
        <svg width="11" height="11" viewBox="0 0 16 16" fill="none" aria-hidden>
          <path d="M4 6.2 8 10.2l4-4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>

      {open && (
        <div className="da-menu" role="menu">
          <button type="button" role="menuitem" onClick={copy}>
            <span className="da-t">Copy page</span>
            <span className="da-d">This page as Markdown</span>
          </button>
          <button type="button" role="menuitem" onClick={viewMarkdown}>
            <span className="da-t">View as Markdown</span>
            <span className="da-d">Open the plain text in a tab</span>
          </button>
          <button type="button" role="menuitem" onClick={() => askAbout('https://claude.ai/new?q=')}>
            <span className="da-t">Open in Claude</span>
            <span className="da-d">Ask about this page</span>
          </button>
          <button type="button" role="menuitem" onClick={() => askAbout('https://chatgpt.com/?q=')}>
            <span className="da-t">Open in ChatGPT</span>
            <span className="da-d">Ask about this page</span>
          </button>
        </div>
      )}
    </div>
  )
}
