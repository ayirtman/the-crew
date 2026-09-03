// Prebuilt kit — extend, don't gut.
import type { ButtonHTMLAttributes, ReactNode } from "react";

const VARIANTS = {
  primary: "bg-indigo-600 text-white shadow-lg shadow-indigo-600/30 hover:bg-indigo-500",
  success: "bg-emerald-500 text-white shadow-lg shadow-emerald-500/30 hover:bg-emerald-400",
  ghost: "border-2 border-slate-300 bg-white/70 text-slate-700 hover:bg-white",
  danger: "bg-rose-500 text-white shadow-lg shadow-rose-500/30 hover:bg-rose-400",
} as const;

/** The tap target: >=64px tall, chunky, springy. One primary per screen. */
export function BigButton({ children, variant = "primary", full = false, className = "", ...rest }: {
  children: ReactNode; variant?: keyof typeof VARIANTS; full?: boolean; className?: string;
} & ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      {...rest}
      className={`min-h-16 rounded-full px-8 text-2xl font-bold transition
        active:scale-95 disabled:opacity-40 disabled:active:scale-100
        focus-visible:outline-4 focus-visible:outline-offset-2 focus-visible:outline-indigo-400
        ${VARIANTS[variant]} ${full ? "w-full" : ""} ${className}`}
    >
      {children}
    </button>
  );
}
