// The site footer. Kept separate from any one page's copy, because several pages
// share it: the portal, the patient and contribute pages, and the legal documents.

export interface NavItem {
  label: string
  href:  string
}

export const FOOTER = {
  tagline: 'Clinician-grade data for medical AI.',
  legal:   '© 2026 Senebiclabs Inc. · All rights reserved',
  legal2:  'Evaluation, benchmarks and training data, reviewed by licensed clinicians.',
  nav: {
    platform: [
      { label: 'For patients',       href: '/patients' },
      { label: 'Specialist network', href: '/experts' },
      { label: 'Contribute data',    href: '/contribute' },
    ] satisfies NavItem[],
    company: [
      { label: 'About', href: '/about' },
      { label: 'Docs',  href: '/docs' },
    ] satisfies NavItem[],
    legal: [
      { label: 'Privacy', href: '/privacy' },
      { label: 'Terms',   href: '/terms' },
    ] satisfies NavItem[],
  },
}
