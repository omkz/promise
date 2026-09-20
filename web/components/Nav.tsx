"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const links = [
  { href: "/", label: "Commitments" },
  { href: "/approvals", label: "Approvals" },
  { href: "/activity", label: "Activity" },
  { href: "/connections", label: "Connections" },
];

export default function Nav() {
  const pathname = usePathname();
  return (
    <nav className="nav">
      {links.map((l) => (
        <Link key={l.href} href={l.href} className={`nav-link ${pathname === l.href ? "active" : ""}`}>
          {l.label}
        </Link>
      ))}
    </nav>
  );
}
