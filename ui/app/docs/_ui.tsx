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

// Field lists, for pages that want to present a request or response shape as a
// table rather than as prose. Nothing uses these yet and no existing page was
// changed to: they are here so the next endpoint page has a refined presentation
// to reach for instead of inventing one.
export function Params({ children }: { children: React.ReactNode }) {
  return <div className="docs-params">{children}</div>
}

export function Param(
  { name, type, required, children }:
  { name: string; type: string; required?: boolean; children: React.ReactNode },
) {
  return (
    <div className="docs-param">
      <span className="n">{name}</span>
      <span className="meta">
        <span>{type}</span>
        {required && <span className="req">required</span>}
      </span>
      <span className="d">{children}</span>
    </div>
  )
}
