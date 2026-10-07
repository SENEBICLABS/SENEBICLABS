import { Instrument_Sans } from 'next/font/google'

// The reference reads as a reference, not as the marketing site. Instrument Sans
// is the face Mintlify-style API docs use, and it is scoped to this route so the
// rest of senebiclabs.com keeps Geist and DM Sans.
const instrument = Instrument_Sans({
  subsets: ['latin'],
  weight: ['400', '500', '600', '700'],
  variable: '--docs-sans',
  display: 'swap',
})

export default function DocsLayout({ children }: { children: React.ReactNode }) {
  return <div className={instrument.variable}>{children}</div>
}
