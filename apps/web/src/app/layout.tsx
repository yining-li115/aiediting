import type { Metadata } from "next";

import { TopBar } from "@/components/layout/TopBar";
import { APP_NAME } from "@/lib/config";

import "./globals.css";

export const metadata: Metadata = {
  title: APP_NAME,
  description: "Upload your footage, describe what you want, get a cut back.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <div className="flex h-screen min-w-0 flex-col overflow-hidden">
          <TopBar />
          <main className="min-h-0 flex-1 overflow-y-auto">{children}</main>
        </div>
      </body>
    </html>
  );
}
