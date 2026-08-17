import type { MetadataRoute } from 'next'

const BASE = 'https://senebiclabs.com'

// Public pages only — the gated /fahimasima tool and API routes are excluded.
const ROUTES = ['', '/about', '/patients', '/experts', '/evaluate', '/docs', '/developers', '/contribute', '/privacy', '/terms']

export default function sitemap(): MetadataRoute.Sitemap {
  const now = new Date()
  return ROUTES.map((path) => ({
    url: `${BASE}${path}`,
    lastModified: now,
    changeFrequency: 'monthly',
    priority: path === '' ? 1 : 0.7,
  }))
}
