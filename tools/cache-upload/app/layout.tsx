import type { Metadata } from "next";
import "./globals.css";
export const metadata: Metadata = {
  title: "Contribute caches · Ascension Archive",
  description:
    "Help preserve Ascension with game data from your WDB and Account folders.",
  robots: { index: false, follow: false },
  referrer: "no-referrer",
  icons: { icon: "/favicon.svg" },
};
export default function Layout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
