import NavBar from '../components/NavBar'
import FooterSection from '../components/sections/FooterSection'

const Label = ({ children }: { children: string }) => (
  <span style={{
    fontFamily: 'Geist Mono, monospace',
    fontSize: 13,
    letterSpacing: '0.14em',
    textTransform: 'uppercase' as const,
    color: 'var(--slate)',
    display: 'block',
    marginBottom: 20,
  }}>
    {children}
  </span>
)

export default function AboutPage() {
  return (
    <>
      <NavBar active="about" />

      {/* HERO  */}
      <section style={{ padding: 'clamp(120px, 15vw, 180px) 0 clamp(48px, 6vw, 80px)' }}>
        <div className="wrap">
          <Label>About us</Label>
          <h1 style={{
            fontFamily: '"SF Pro Display", -apple-system, BlinkMacSystemFont, system-ui, sans-serif',
            fontWeight: 100,
            fontSize: 'clamp(36px, 6.5vw, 88px)',
            lineHeight: 1.05,
            letterSpacing: '0.025em',
            maxWidth: 860,
            marginBottom: 48,
          }}>
            We believe every disease can be detected before it takes hold.
          </h1>
          <p style={{
            fontWeight: 300,
            fontSize: 'clamp(17px, 1.9vw, 22px)',
            color: 'var(--slate)',
            lineHeight: 1.8,
            maxWidth: 580,
          }}>
            Senebiclabs is a biological intelligence company. We are building a system
            that understands what a healthy human body looks like at its most
            fundamental level, so that anything departing from it can be seen,
            described, and eventually corrected. We start with the lung.
          </p>
        </div>
      </section>

      {/* THE STORY  */}
      <section style={{ padding: 'clamp(64px, 8vw, 100px) 0', borderTop: '1px solid var(--hairline)' }}>
        <div className="wrap">
          <div className="about-2col">
            <div style={{ position: 'sticky', top: 120 } as React.CSSProperties}>
              <Label>Where we started</Label>
              <h2 style={{
                fontFamily: '"SF Pro Display", -apple-system, BlinkMacSystemFont, system-ui, sans-serif',
                fontWeight: 100,
                fontSize: 'clamp(24px, 2.8vw, 38px)',
                lineHeight: 1.2,
                letterSpacing: '0.02em',
              }}>
                A question that had no answer.
              </h2>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 28 }}>
              <p style={{ fontSize: 'clamp(17px, 1.6vw, 20px)', lineHeight: 1.85, color: 'var(--ink)', fontWeight: 300 }}>
                We started with a question that sounds deceptively simple: what does a
                healthy human body actually look like? Not in general terms. Not as a
                textbook diagram. But precisely, at the level of individual cells,
                across real people, with all their variation.
              </p>
              <p style={{ fontSize: 'clamp(16px, 1.5vw, 19px)', lineHeight: 1.85, color: 'var(--slate)', fontWeight: 300 }}>
                Nobody had a complete answer. Medicine has always worked the other way around:
                studying disease to understand what went wrong, rather than studying health
                to know what right looks like. That gap is the reason diagnoses come late.
                It is the reason patients get mismatched. It is the reason treatments
                are designed for averages rather than individuals.
              </p>
              <p style={{ fontSize: 'clamp(16px, 1.5vw, 19px)', lineHeight: 1.85, color: 'var(--slate)', fontWeight: 300 }}>
                We decided to build the reference that was missing. Starting with the
                respiratory system, where the data existed to do it properly, we built
                a biological model of what healthy looks like at the cellular level.
                Everything computed from real human cells. Nothing hardcoded.
                Nothing curated by hand.
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* WHAT WE'RE BUILDING  */}
      <section style={{ padding: 'clamp(64px, 8vw, 100px) 0', borderTop: '1px solid var(--hairline)' }}>
        <div className="wrap">
          <div className="about-2col">
            <div style={{ position: 'sticky', top: 120 } as React.CSSProperties}>
              <Label>What we are building</Label>
              <h2 style={{
                fontFamily: '"SF Pro Display", -apple-system, BlinkMacSystemFont, system-ui, sans-serif',
                fontWeight: 100,
                fontSize: 'clamp(24px, 2.8vw, 38px)',
                lineHeight: 1.2,
                letterSpacing: '0.02em',
              }}>
                A platform that connects the science to the people who need it.
              </h2>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 28 }}>
              <p style={{ fontSize: 'clamp(17px, 1.6vw, 20px)', lineHeight: 1.85, color: 'var(--ink)', fontWeight: 300 }}>
                The intelligence engine is the core. It takes a biological sample and
                describes precisely what has changed from healthy: which cell populations
                are out of proportion, which biological processes are disrupted, and how
                far each finding sits from the normal range. Clear. Traceable. Grounded
                in real human biology.
              </p>
              <p style={{ fontSize: 'clamp(16px, 1.5vw, 19px)', lineHeight: 1.85, color: 'var(--slate)', fontWeight: 300 }}>
                On top of that, we are building a network of respiratory physicians
                and researchers who interact with the model's findings, add
                clinical judgment, and help it get sharper over time. The model does not
                name diseases. Specialists do. We are deliberate about that boundary.
              </p>
              <p style={{ fontSize: 'clamp(16px, 1.5vw, 19px)', lineHeight: 1.85, color: 'var(--slate)', fontWeight: 300 }}>
                And for patients, we are building a portal: describe your symptoms,
                get matched to the right specialist for your specific situation.
                Not a generic referral. A match built on what the biology suggests.
              </p>
              <p style={{ fontSize: 'clamp(16px, 1.5vw, 19px)', lineHeight: 1.85, color: 'var(--slate)', fontWeight: 300 }}>
                The respiratory system is where we start. Every organ system follows.
                That is the scope of what we are working toward.
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* HOW WE WORK  */}
      <section style={{ padding: 'clamp(64px, 8vw, 100px) 0', borderTop: '1px solid var(--hairline)' }}>
        <div className="wrap">
          <Label>How we work</Label>
          <h2 style={{
            fontFamily: '"SF Pro Display", -apple-system, BlinkMacSystemFont, system-ui, sans-serif',
            fontWeight: 100,
            fontSize: 'clamp(26px, 3.5vw, 44px)',
            lineHeight: 1.15,
            letterSpacing: '0.02em',
            marginBottom: 56,
            maxWidth: 540,
          }}>
            A few things we refuse to compromise on.
          </h2>
          <div className="about-3col" style={{ borderTop: '1px solid var(--hairline)', paddingTop: 'clamp(36px, 5vw, 56px)' }}>
            {[
              {
                n: 'I',
                title: 'The data decides',
                body: 'Nothing biological is hardcoded. No manually written rules, no curated assumptions. Every reference statistic is computed from real human cells. If we cannot derive it from the data, we do not use it.',
              },
              {
                n: 'II',
                title: 'The model describes. People judge.',
                body: 'The system surfaces deviations. Clinicians interpret them. We are strict about this line: the model does not diagnose and we will never design it to. Clinical judgment belongs to clinicians.',
              },
              {
                n: 'III',
                title: 'Get the foundation right first',
                body: 'Patient matching is only as good as the model beneath it. The model is only as good as the clinical feedback that refines it. We build in sequence and do not rush any layer to serve another.',
              },
            ].map((c) => (
              <div key={c.n} style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
                <span style={{
                  fontFamily: 'Geist Mono, monospace',
                  fontSize: 12,
                  color: 'var(--slate)',
                  letterSpacing: '0.14em',
                  opacity: 0.5,
                }}>
                  {c.n}
                </span>
                <h3 style={{
                  fontFamily: 'Geist, sans-serif',
                  fontSize: 'clamp(17px, 1.5vw, 20px)',
                  fontWeight: 500,
                  letterSpacing: '-0.01em',
                  lineHeight: 1.3,
                  color: 'var(--ink)',
                }}>
                  {c.title}
                </h3>
                <p style={{ fontSize: 'clamp(14px, 1.2vw, 15px)', lineHeight: 1.85, color: 'var(--slate)', fontWeight: 300 }}>
                  {c.body}
                </p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <FooterSection />
    </>
  )
}
