// Prebuilt kit — extend, don't gut.
import type { ReactNode } from "react";

/** One concern per card: rounded, soft-shadowed white surface. */
export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <section className={`rounded-3xl bg-white/95 p-6 shadow-xl shadow-slate-900/10 ${className}`}>
      {children}
    </section>
  );
}
