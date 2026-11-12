import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "plantsight",
  description: "Arcwell Electronics 生產數據助理：良率、不良率、準交率，一句話問",
};

export const viewport: Viewport = {
  themeColor: "#2b5d8a",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="zh-Hant-TW" className="h-full antialiased">
      <body className="min-h-full">{children}</body>
    </html>
  );
}
