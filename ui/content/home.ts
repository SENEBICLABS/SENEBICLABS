// Landing page, platform pitch.
// Talks about the value Senebiclabs brings overall.
// Each audience has their own detail page.

export interface NavItem {
  label: string
  href:  string
}

export interface AudienceCard {
  tag:     string
  heading: string
  body:    string
  cta:     string
  href:    string
  appLinks?: { ios: string; android: string }
}

export interface ValuePoint {
  heading: string
  body:    string
}

// HERO

export const HERO = {
  tagline: 'Connecting Patients, Intelligence, and Experts for Better Health Care.',
  mission: 'Correct every deviation from health.',
  sub:     'We\'re entering a new era of medicine, one where every human disease can be detected, understood, and ultimately corrected. At Senebiclabs, we\'re building the biological intelligence to make that possible, starting with the human lung.',
  p1:      'Senebiclabs is building a biological reference model of the human body, starting with the lung. A system that knows what healthy looks like at the cellular level and can describe precisely what has changed.',
  p2:      'The reference is built from the Human Lung Cell Atlas, covering donors, tissues, and conditions across the full range of healthy lung biology. The model learns cell type composition, gene expression distributions, and spatial organisation. Nothing is hardcoded. Every statistic comes from real human cells.',
  p3:      'When a new sample enters the system, the model returns ranked deviations: which cell populations are expanded or depleted, which genes fall outside their reference range, and which biological pathways are dysregulated. The output is traceable to its source. Experts interpret it. The model does not guess.',
}

// LATEST

export const LATEST = {
  label: 'Latest from Senebiclabs',
  cards: [
    {
      tag:     'Announcement',
      heading: 'Senebiclabs launches the first biological reference model of the healthy human lung.',
    },
    {
      tag:     'Platform',
      heading: 'The deviation detection engine is now open to research laboratories via API.',
    },
    {
      tag:     'Network',
      heading: 'We are now onboarding specialists. Join the founding cohort and be part of building the network.',
    },
  ],
}

// GOAL

export const GOAL = {
  label: 'our goal',
  body:  'We are using machine learning to detect every deviation from human health, understand what caused it, and build the biological foundation to correct it.',
  cta:   'explore the science',
  href:  '/about',
}

// THREE BLOCKS

export const BLOCKS = [
  {
    label:   'OUR SCIENCE',
    heading: 'A biological reference model of the healthy human lung, built from real donor data.',
    link:    '',
    href:    '',
  },
  {
    label:   'OUR MISSION',
    heading: 'Understand every deviation from human health. Build the biological foundation to detect, describe, and correct every disease.',
    link:    '',
    href:    '',
  },
  {
    label:   'OUR NETWORK',
    heading: 'We are building a network of clinicians and researchers, contributing the expertise that sharpens the model and reaching the patients who need them. Join the founding cohort.',
    link:    '',
    href:    '',
  },
]

// SENEBICLABS SECTION

export const SENEBICLABS_SECTION = {
  label: 'Senebiclabs',
  p1: 'Senebiclabs is here to solve every human disease and improve healthcare, by building a complete biological reference model of the human body. We start with the lung. We do not stop there.',
  p2: 'Our team of computational biologists and machine learning engineers is building a system that understands what healthy human tissue looks like at the cellular level, and can describe precisely what has changed in any new sample, with full statistical traceability.',
  p3: 'Every model we refine, and every expert annotation we collect, brings us closer to our mission: to detect, characterise, and ultimately correct every disease that departs from the healthy human baseline, through better diagnostics, targeted treatment, and precision drug discovery.',
}

// FOOTER

export const FOOTER = {
  tagline: 'Starting with the lung. Building toward every disease.',
  legal:   '© 2026 Senebiclabs Inc. · All rights reserved',
  legal2:  'Starting with the lung. Building toward the whole body.',
  nav: {
    platform: [
      { label: 'For patients',       href: '/patients' },
      { label: 'Specialist network', href: '/experts' },
      { label: 'Contribute data',    href: '/contribute' },
    ] satisfies NavItem[],
    company: [
      { label: 'About', href: '/about' },
      { label: 'Research', href: '/research' },
    ] satisfies NavItem[],
    legal: [
      { label: 'Privacy', href: '/privacy' },
      { label: 'Terms',   href: '/terms' },
    ] satisfies NavItem[],
  },
}
