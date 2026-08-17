import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  metadataBase: new URL("https://senebiclabs.com"),
  title: {
    default: "Senebiclabs: Biological intelligence, starting with respiratory",
    template: "%s · Senebiclabs",
  },
  description:
    "A biological intelligence platform connecting patients, clinicians, and the research community. Currently focused on the respiratory system.",
  applicationName: "Senebiclabs",
  keywords: [
    "respiratory health", "biological intelligence", "lung", "specialist matching",
    "single-cell", "research", "Senebiclabs",
  ],
  alternates: { canonical: "/" },
  openGraph: {
    type: "website",
    url: "https://senebiclabs.com",
    siteName: "Senebiclabs",
    title: "Senebiclabs: Biological intelligence, starting with respiratory",
    description:
      "Connecting patients, clinicians, and researchers. Currently focused on the respiratory system.",
  },
  twitter: {
    card: "summary",
    title: "Senebiclabs: Biological intelligence, starting with respiratory",
    description:
      "Connecting patients, clinicians, and researchers. Currently focused on the respiratory system.",
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
