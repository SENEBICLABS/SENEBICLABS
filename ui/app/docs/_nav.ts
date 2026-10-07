// The reference is a set of pages, not one scroll. This is the order of them:
// the sidebar, the previous/next links and the search index all read from here,
// so adding a page means editing one list.

export type DocPage = { slug: string; label: string; group?: string }

export const PAGES: DocPage[] = [
  { slug: '', label: 'Overview' },
  { slug: 'quickstart', label: 'Quickstart' },
  { slug: 'authentication', label: 'Authentication' },

  { slug: 'create-a-project', label: 'Create a project', group: 'Endpoints' },
  { slug: 'push-items', label: 'Push items' },
  { slug: 'poll-results', label: 'Poll results' },
  { slug: 'compare-versions', label: 'Compare versions' },
  { slug: 'failure-library', label: 'Failure library' },
  { slug: 'webhooks', label: 'Webhooks' },

  { slug: 'task-config', label: 'Task config', group: 'Reference' },
  { slug: 'errors', label: 'Errors and notes' },
]

export const href = (slug: string) => (slug ? `/docs/${slug}` : '/docs')
