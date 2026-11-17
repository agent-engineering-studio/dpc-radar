import type { Metadata, Viewport } from "next";
import "leaflet/dist/leaflet.css";
import "./globals.css";

export const metadata: Metadata = {
  title: "dpc-radar · the rain over Italy",
  description:
    "The demo of dpc-radar: the open radar data of the Italian Civil Protection on a map. Click anywhere to read the rain.",
};

export const viewport: Viewport = { width: "device-width", initialScale: 1 };

export default function Layout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
