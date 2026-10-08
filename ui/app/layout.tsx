import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  metadataBase: new URL("https://senebiclabs.com"),
  title: {
    default: "Senebiclabs: Clinician-grade data for medical AI",
    template: "%s · Senebiclabs",
  },
  description:
    "Licensed clinicians evaluate, correct and create the data medical AI is trained and measured against, with the consensus, adjudication and provenance that make it trustworthy enough to build on.",
  applicationName: "Senebiclabs",
  keywords: [
    "medical AI evaluation", "clinical evaluation", "training data", "benchmarks",
    "RLHF", "preference data", "clinician review", "Senebiclabs",
  ],
  alternates: { canonical: "/" },
  openGraph: {
    type: "website",
    url: "https://senebiclabs.com",
    siteName: "Senebiclabs",
    title: "Senebiclabs: Clinician-grade data for medical AI",
    description:
      "Licensed clinicians evaluate, correct and create the data medical AI is trained and measured against.",
  },
  twitter: {
    card: "summary",
    title: "Senebiclabs: Clinician-grade data for medical AI",
    description:
      "Licensed clinicians evaluate, correct and create the data medical AI is trained and measured against.",
  },
  robots: { index: true, follow: true },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link
          rel="preconnect"
          href="https://fonts.gstatic.com"
          crossOrigin=""
        />
        <link
          rel="stylesheet"
          href="https://fonts.googleapis.com/css2?family=Geist:wght@300;400;500;600;700&family=Geist+Mono:wght@400;500&family=DM+Sans:wght@300;400;500&display=swap"
        />
      </head>
      <body>{children}</body>
    </html>
  );
}
