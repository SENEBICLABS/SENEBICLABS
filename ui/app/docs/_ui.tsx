// Shared pieces for every page of the reference.

export const BASE = 'https://api.senebiclabs.com/api/v1/project'

export function Code({ children }: { children: string }) {
  return (
    <pre className="docs-pre">
      <code>{children}</code>
    </pre>
  )
}

export function C({ children }: { children: string }) {
  return <code className="ic">{children}</code>
}
