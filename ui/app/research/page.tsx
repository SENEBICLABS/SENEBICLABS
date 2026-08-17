import NavBar from '../components/NavBar'
import HeroSection from '../components/sections/HeroSection'
import GoalSection from '../components/sections/GoalSection'
import BlocksSection from '../components/sections/BlocksSection'
import SenebiclabsSection from '../components/sections/SenebiclabsSection'
import FooterSection from '../components/sections/FooterSection'

function ContributeSection() {
  return (
    <section style={{ padding: 'clamp(64px, 10vw, 120px) 0', borderTop: '1px solid var(--hairline)' }}>
      <div className="wrap">

        <span style={{
          fontFamily: 'Geist Mono, monospace',
          fontSize: 10,
          letterSpacing: '0.18em',
          textTransform: 'uppercase' as const,
          color: 'var(--slate)',
          border: '1px solid var(--hairline)',
          padding: '4px 10px',
          borderRadius: 999,
          display: 'inline-block',
          marginBottom: 28,
        }}>
          For clinicians and researchers
        </span>

        <h2 style={{
          fontFamily: '"SF Pro Display", -apple-system, BlinkMacSystemFont, system-ui, sans-serif',
          fontWeight: 100,
          fontSize: 'clamp(32px, 4vw, 56px)',
          letterSpacing: '0.03em',
          lineHeight: 1.1,
          marginBottom: 28,
          maxWidth: 700,
        }}>
          Contribute your clinical knowledge. Get paid for it.
        </h2>

        <p style={{
          fontSize: 'clamp(17px, 1.7vw, 20px)',
          fontWeight: 300,
          color: 'var(--ink)',
          lineHeight: 1.8,
          maxWidth: 640,
          marginBottom: 28,
        }}>
          The Senebiclabs model improves with every expert annotation. Clinicians and researchers
          review model outputs, confirm or correct deviation findings, and annotate cases.
          Each contribution sharpens the detection engine and expands the clinical knowledge base.
        </p>

        <p style={{
          fontSize: 'clamp(15px, 1.4vw, 17px)',
          fontWeight: 300,
          color: 'var(--ink)',
          lineHeight: 1.8,
          maxWidth: 560,
          marginBottom: 52,
        }}>
          Remote. Async. Paid per task. No minimum commitment.
        </p>

        <div style={{ display: 'flex', gap: 20, alignItems: 'center' }}>
          <a href="/contribute" className="nav-join-cta">Join the waitlist →</a>
        </div>

      </div>
    </section>
  )
}

function SpecialistSection() {
  return (
    <section style={{ padding: 'clamp(64px, 10vw, 120px) 0', borderTop: '1px solid var(--hairline)' }}>
      <div className="wrap" style={{ textAlign: 'center' }}>

        <span style={{
          fontFamily: 'Geist Mono, monospace',
          fontSize: 10,
          letterSpacing: '0.18em',
          textTransform: 'uppercase' as const,
          color: 'var(--teal)',
          border: '1px solid rgba(255,255,255,0.2)',
          padding: '4px 10px',
          borderRadius: 999,
          display: 'inline-block',
          marginBottom: 28,
        }}>
          Now onboarding doctors
        </span>

        <h2 style={{
          fontFamily: '"SF Pro Display", -apple-system, BlinkMacSystemFont, system-ui, sans-serif',
          fontWeight: 100,
          fontSize: 'clamp(32px, 4vw, 56px)',
          letterSpacing: '0.03em',
          lineHeight: 1.1,
          marginBottom: 32,
        }}>
          Doctors, we need you.
        </h2>

        <p style={{
          fontSize: 'clamp(18px, 1.8vw, 22px)',
          fontWeight: 300,
          color: 'var(--ink)',
          lineHeight: 1.7,
          maxWidth: 680,
          margin: '0 auto 56px',
        }}>
          Millions of patients never reach the right doctor. They wait, they get mismatched, they get worse. You have the expertise to change that. Join the Senebiclabs network and we will make sure the patients who need you most can actually find you.
        </p>

        <div style={{ display: 'flex', gap: 20, justifyContent: 'center', marginBottom: 'clamp(48px, 8vw, 100px)' }}>
          <a href="/experts" className="nav-join-cta">Apply to join →</a>
        </div>

        <div style={{ borderTop: '1px solid var(--hairline)', paddingTop: 64, textAlign: 'left', maxWidth: 680, margin: '0 auto' }}>
          <span className="iso-label" style={{ display: 'block', marginBottom: 24 }}>Coming soon</span>
          <p style={{ fontSize: 'clamp(18px, 1.8vw, 22px)', fontWeight: 300, color: 'var(--ink)', lineHeight: 1.75, marginBottom: 20 }}>
            We are rolling out the Senebiclabs app for patients. Describe your symptoms, get matched to the nearest suitable specialist, and book a consultation, all from your phone on iOS and Android.
          </p>
          <p style={{ fontSize: 'clamp(15px, 1.4vw, 17px)', color: 'var(--ink)', lineHeight: 1.75 }}>
            Join the specialist network now and be discoverable to patients the moment the app launches.
          </p>
        </div>

      </div>
    </section>
  )
}

export default function HomePage() {
  return (
    <>
      <NavBar />
      <HeroSection />
      <GoalSection />
      <BlocksSection />
      <SpecialistSection />
      <ContributeSection />
      <SenebiclabsSection />
      <FooterSection />
    </>
  )
}
