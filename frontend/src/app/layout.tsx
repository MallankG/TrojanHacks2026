import type { Metadata, Viewport } from "next";
import "@fontsource/ibm-plex-sans/400.css";
import "@fontsource/ibm-plex-sans/500.css";
import "@fontsource/ibm-plex-sans/600.css";
import "@fontsource/ibm-plex-sans/700.css";
import "@fontsource/jetbrains-mono/400.css";
import "@fontsource/jetbrains-mono/500.css";
import "./globals.css";
import { Toaster } from "@/components/shell/toaster";

export const metadata: Metadata = {
  title: { default: "TraceLayer", template: "%s · TraceLayer" },
  description: "Governed financial-crime investigation layer. Prototype on synthetic data; decision support for human review.",
};

export const viewport: Viewport = {
  themeColor: "#0b1626",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full">
        <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:left-3 focus:top-3 focus:z-[100] focus:rounded-lg focus:bg-white focus:px-4 focus:py-2 focus:font-semibold">
          Skip to content
        </a>
        {children}
        <Toaster />
      </body>
    </html>
  );
}
