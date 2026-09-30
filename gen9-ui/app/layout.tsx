import type { Metadata, Viewport } from "next";
import localFont from "next/font/local";
import { headers } from "next/headers";

import { Providers } from "@/components/providers/providers";

import "./globals.css";

// Instrument Sans (OFL), self-hosted from gen9-design: no third-party font request at runtime
const instrumentSans = localFont({
  src: "./fonts/InstrumentSans-Variable.woff2",
  variable: "--font-instrument",
  weight: "400 700",
  display: "swap",
});

export const metadata: Metadata = {
  title: { default: "Gen9", template: "%s – Gen9" },
  description: "Get any task done with autonomous agents. Configure your own: what they know, the tools they use, and what they may do without asking.",
  applicationName: "Gen9",
  appleWebApp: { title: "Gen9", statusBarStyle: "default" },
  formatDetection: { telephone: false },
};

// Mobile first: cover the whole screen (content respects safe areas), themed browser chrome
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  colorScheme: "light dark",
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#F6F7F9" },
    { media: "(prefers-color-scheme: dark)", color: "#0D0F16" },
  ],
};

export default async function RootLayout({ children }: LayoutProps<"/">) {
  // Per-request CSP nonce from proxy.ts, for the one inline script we render (theme bootstrap)
  const nonce = (await headers()).get("x-nonce") ?? undefined;
  return (
    <html lang="en" className={instrumentSans.variable} suppressHydrationWarning>
      <body className="min-h-dvh antialiased">
        {/* Own stacking context (Base UI): portaled popups always sit above page content */}
        <div className="root isolate">
          <Providers nonce={nonce}>{children}</Providers>
        </div>
      </body>
    </html>
  );
}
