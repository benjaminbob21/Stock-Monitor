import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Stock-Monitor",
  description:
    "Explainable, human-in-the-loop stock conviction scoring. You execute every trade.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
