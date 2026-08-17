import LegalDoc, { Section } from '../components/LegalDoc'

export const metadata = {
  title: 'Terms of Service · Senebiclabs',
  description: 'The terms that govern your use of the Senebiclabs website and tools.',
}

export default function TermsPage() {
  return (
    <LegalDoc
      title="Terms of Service"
      updated="4 June 2026"
      intro="These terms govern your use of the Senebiclabs website and tools. By using the
      website or submitting your information, you agree to these terms. Please read them carefully."
    >
      <Section heading="What Senebiclabs is">
        <p>
          Senebiclabs is building a biological intelligence platform for respiratory health, starting
          with the lung. This website is currently a pre-launch service: it lets patients, specialists,
          and researchers register interest, and gives approved research partners access to an early
          analysis tool. Features and availability may change.
        </p>
      </Section>

      <Section heading="Not medical advice">
        <p>
          Senebiclabs does not provide medical advice, diagnosis, or treatment, and does not create a
          doctor-patient relationship. Any information or output from our tools is for research and
          informational purposes only and must be interpreted by a qualified professional. Always seek
          the advice of a licensed clinician for any medical condition. Never disregard or delay medical
          care because of something you read or generate here.
        </p>
      </Section>

      <Section heading="Research-use-only tools">
        <p>
          The analysis tool is an early-stage research prototype. It describes statistical deviations
          from a healthy reference; it does not name diseases and is not a diagnostic device. It has
          not been clinically validated. Outputs are candidate findings for expert review and must not
          be used as the basis for clinical decisions.
        </p>
      </Section>

      <Section heading="Eligibility">
        <p>
          You must be at least 16 years old to use this website. If you apply to the specialist network,
          you confirm that you are a licensed medical professional and that the information you provide
          is accurate. We may verify credentials before granting access.
        </p>
      </Section>

      <Section heading="Your responsibilities">
        <ul style={{ paddingLeft: 22, display: 'flex', flexDirection: 'column', gap: 8 }}>
          <li>Provide accurate information and keep it up to date.</li>
          <li>Do not submit identifiable patient health information or anyone&rsquo;s data without proper authorisation.</li>
          <li>Do not misuse, disrupt, or attempt to gain unauthorised access to the service.</li>
          <li>Use the tools lawfully and only for legitimate research or onboarding purposes.</li>
        </ul>
      </Section>

      <Section heading="Intellectual property">
        <p>
          The website, software, models, and content are owned by Senebiclabs and protected by
          applicable law. We grant you a limited, non-exclusive right to use the service as intended.
          You may not copy, resell, or reverse-engineer the platform without our permission.
        </p>
      </Section>

      <Section heading="Third-party services">
        <p>
          The service relies on third-party providers (such as hosting, database, and email services).
          Their availability and terms are outside our control, and we are not responsible for their acts
          or omissions.
        </p>
      </Section>

      <Section heading="Disclaimers">
        <p>
          The service is provided &ldquo;as is&rdquo; and &ldquo;as available&rdquo;, without warranties of any kind,
          express or implied, including fitness for a particular purpose, accuracy, or non-infringement.
          We do not warrant that the service will be uninterrupted, error-free, or that any output is
          correct or complete.
        </p>
      </Section>

      <Section heading="Limitation of liability">
        <p>
          To the fullest extent permitted by law, Senebiclabs and its team will not be liable for any
          indirect, incidental, special, or consequential damages, or for any loss arising from your use
          of, or reliance on, the service or its outputs. Our total liability for any claim is limited to
          the amount you paid us for the service, if any.
        </p>
      </Section>

      <Section heading="Changes and termination">
        <p>
          We may update, suspend, or discontinue any part of the service, and may revise these terms.
          We will update the &ldquo;last updated&rdquo; date above. Continued use after changes means you accept
          the updated terms.
        </p>
      </Section>

      <Section heading="Governing law">
        <p>
          These terms are governed by the laws of the jurisdiction in which Senebiclabs is established,
          without regard to conflict-of-law principles. Disputes will be handled in the courts of that
          jurisdiction.
        </p>
      </Section>

      <Section heading="Contact">
        <p>
          Questions about these terms? Email{' '}
          <a href="mailto:godwinyampoi449@gmail.com" style={{ color: 'var(--ink)', textDecoration: 'underline' }}>
            godwinyampoi449@gmail.com
          </a>.
        </p>
      </Section>
    </LegalDoc>
  )
}
