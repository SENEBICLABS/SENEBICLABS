import LegalDoc, { Section } from '../components/LegalDoc'

export const metadata = {
  title: 'Privacy Policy · Senebiclabs',
  description: 'How Senebiclabs collects, uses, and protects your information.',
}

export default function PrivacyPage() {
  return (
    <LegalDoc
      title="Privacy Policy"
      updated="4 June 2026"
      intro="Senebiclabs respects your privacy. This policy explains what information we
      collect when you use our website, why we collect it, how we use it, and the choices
      you have. We collect only what we need to operate a pre-launch onboarding service."
    >
      <Section heading="Who we are">
        <p>
          Senebiclabs Inc. (&ldquo;Senebiclabs&rdquo;, &ldquo;we&rdquo;, &ldquo;us&rdquo;) operates the website at
          senebiclabs.com. For any privacy questions or requests, contact us at{' '}
          <a href="mailto:godwinyampoi449@gmail.com" style={{ color: 'var(--ink)', textDecoration: 'underline' }}>
            godwinyampoi449@gmail.com
          </a>.
        </p>
      </Section>

      <Section heading="Information we collect">
        <p>We collect only the information you choose to give us:</p>
        <ul style={{ marginTop: 12, paddingLeft: 22, display: 'flex', flexDirection: 'column', gap: 8 }}>
          <li><strong>Contact details</strong>, the email address, name, and (for specialists) specialty you submit when you join a waitlist or apply to the network.</li>
          <li><strong>Approximate location</strong>, if you grant permission, we may record approximate coordinates from your browser to help match patients with nearby specialists. This is optional and you can decline.</li>
          <li><strong>Messages</strong>, anything you send us by email.</li>
          <li><strong>Basic technical data</strong>, standard server and analytics information such as IP address and browser type, used to keep the service secure and working.</li>
        </ul>
        <p style={{ marginTop: 12 }}>
          We do not ask for, and you should not submit, medical records or identifiable patient
          health information through this website.
        </p>
      </Section>

      <Section heading="How we use your information">
        <ul style={{ paddingLeft: 22, display: 'flex', flexDirection: 'column', gap: 8 }}>
          <li>To contact you about your waitlist sign-up or application, and when the product launches.</li>
          <li>To verify specialist credentials before adding them to the network.</li>
          <li>To match patients with appropriate specialists (where location is provided).</li>
          <li>To operate, secure, and improve the service.</li>
        </ul>
        <p style={{ marginTop: 12 }}>
          We do not sell your personal information. We do not use it for advertising.
        </p>
      </Section>

      <Section heading="Legal basis">
        <p>
          We process your information based on your consent (which you give by submitting a form)
          and our legitimate interest in operating and securing the service. You may withdraw
          consent at any time by contacting us.
        </p>
      </Section>

      <Section heading="Service providers">
        <p>We share information only with the providers that help us run the service:</p>
        <ul style={{ marginTop: 12, paddingLeft: 22, display: 'flex', flexDirection: 'column', gap: 8 }}>
          <li><strong>Supabase</strong>, secure database storage for sign-ups and applications.</li>
          <li><strong>Resend</strong>, sending confirmation and notification emails.</li>
          <li><strong>Vercel</strong>, website hosting.</li>
          <li><strong>Google Cloud</strong>, backend hosting.</li>
        </ul>
        <p style={{ marginTop: 12 }}>
          These providers process data on our behalf under their own security and privacy terms.
          We may also disclose information if required by law.
        </p>
      </Section>

      <Section heading="Data retention">
        <p>
          We keep your information for as long as needed to operate the service and contact you
          about the launch, or until you ask us to delete it. When you request deletion, we remove
          your personal information from our active systems.
        </p>
      </Section>

      <Section heading="Your rights">
        <p>
          You can ask us to access, correct, or delete the personal information we hold about you,
          and to stop contacting you. To make any of these requests, email{' '}
          <a href="mailto:godwinyampoi449@gmail.com" style={{ color: 'var(--ink)', textDecoration: 'underline' }}>
            godwinyampoi449@gmail.com
          </a>{' '}and we will respond promptly. Depending on where you live, you may have additional
          rights under laws such as the GDPR or similar regulations.
        </p>
      </Section>

      <Section heading="Security">
        <p>
          We use reasonable technical and organisational measures to protect your information,
          including encrypted connections and access-controlled storage. No method of transmission
          or storage is completely secure, but we work to protect your data and limit who can access it.
        </p>
      </Section>

      <Section heading="Children">
        <p>
          This service is not directed to children under 16, and we do not knowingly collect their
          information. If you believe a child has provided us information, contact us and we will delete it.
        </p>
      </Section>

      <Section heading="Not medical advice">
        <p>
          Senebiclabs provides research and matching tools. Nothing on this website is medical advice,
          a diagnosis, or a substitute for care from a qualified clinician. Always consult a healthcare
          professional for medical concerns.
        </p>
      </Section>

      <Section heading="Changes to this policy">
        <p>
          We may update this policy as the service evolves. We will revise the &ldquo;last updated&rdquo;
          date above, and significant changes will be communicated where appropriate.
        </p>
      </Section>

      <Section heading="Contact">
        <p>
          Questions about this policy or your data? Email{' '}
          <a href="mailto:godwinyampoi449@gmail.com" style={{ color: 'var(--ink)', textDecoration: 'underline' }}>
            godwinyampoi449@gmail.com
          </a>.
        </p>
      </Section>
    </LegalDoc>
  )
}
