import "./styles.css";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "PROMISE — Follow Through Agent",
  description: "Remember what you said you'd do, and help you follow through.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
