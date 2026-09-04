import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "NetReto · Empleo público",
  description: "Seguimiento de convocatorias de empleo público.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="es">
      <body>{children}</body>
    </html>
  );
}
