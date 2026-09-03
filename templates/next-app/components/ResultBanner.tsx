// Prebuilt kit — extend, don't gut.
import type { ReactNode } from "react";

const VARIANTS = {
  success: "bg-emerald-100 text-emerald-800",
  error: "bg-rose-100 text-rose-800",
  info: "bg-sky-100 text-sky-800",
} as const;

/** Big feedback strip. role=status so tests and screen readers see it announce. */
export function ResultBanner({ children, variant = "info" }: {
  children: ReactNode; variant?: keyof typeof VARIANTS;
}) {
  return (
    <div role="status" className={`kit-rise rounded-2xl px-6 py-4 text-center text-2xl font-bold ${VARIANTS[variant]}`}>
      {children}
    </div>
  );
}
