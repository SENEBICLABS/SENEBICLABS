import type { MetadataRoute } from 'next'

const BASE = 'https://senebiclabs.com'

// Public pages only — the gated /fahimasima tool and API routes are excluded.
const ROUTES = ['', '/about', '/patients', '/experts', '/evaluate', '/docs', '/developers', '/contribute', '/privacy', '/terms']

// The API reference is a page per endpoint, so each one is its own URL.
const DOC_ROUTES = [
  '/docs/quickstart',
  '/docs/authentication',
  '/docs/create-a-project',
  '/docs/push-items',
  '/docs/poll-results',
  '/docs/compare-versions',
  '/docs/failure-library',
  '/docs/webhooks',
  '/docs/task-config',
  '/docs/errors',
]

export default function sitemap(): MetadataRoute.Sitemap {
  const now = new Date()
  return [...ROUTES, ...DOC_ROUTES].map((path) => ({
    url: `${BASE}${path}`,
    lastModified: now,
    changeFrequency: 'monthly',
    priority: path === '' ? 1 : path.startsWith('/docs/') ? 0.5 : 0.7,
  }))
}
