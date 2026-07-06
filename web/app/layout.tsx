import type { Metadata, Viewport } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import "./globals.css";

// UI typeface — Inter for crisp, professional labels and prose.
const sans = Inter({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-inter",
});

// Numeric / data typeface — a monospaced face gives prices, scores and
// SHAP values fixed-width, terminal-grade alignment.
const mono = JetBrains_Mono({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-jbmono",
});

export const metadata: Metadata = {
  title: "Stock-Monitor",
  description:
    "Explainable, human-in-the-loop stock conviction scoring. You execute every trade.",
  manifest: "/manifest.webmanifest",
  appleWebApp: {
    capable: true,
    statusBarStyle: "black-translucent",
    title: "Stock-Monitor",
  },
  icons: {
    icon: "/icons/icon.svg",
    apple: "/icons/icon.svg",
  },
};

export const viewport: Viewport = {
  themeColor: "#080b13",
  width: "device-width",
  initialScale: 1,
  maximumScale: 1,
  userScalable: false,
  viewportFit: "cover",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className={`${sans.variable} ${mono.variable}`}>
      <body>{children}</body>
    </html>
  );
}
