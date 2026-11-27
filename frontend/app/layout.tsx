import type { Metadata } from "next";
import "./client.css";

export const metadata: Metadata = {
  title: "Pawprint | Pet Supply Operations",
  description:
    "Demand forecasting, replenishment decisions and purchasing workflows for pet retail operations.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
