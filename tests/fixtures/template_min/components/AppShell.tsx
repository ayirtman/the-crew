// Prebuilt kit — extend, don't gut. Compose these components; do not rewrite them.
import type { ReactNode } from "react";

const PALETTES = {
  sky: "from-sky-300 via-cyan-200 to-emerald-200",
  sunset: "from-amber-200 via-orange-200 to-rose-300",
  meadow: "from-lime-200 via-emerald-200 to-teal-300",
  berry: "from-fuchsia-200 via-purple-200 to-indigo-300",
} as const;

export type Palette = keyof typeof PALETTES;

/** Full-screen colorful stage: gradient ground, centered column, safe padding. */
export function AppShell({ children, palette = "sky", title }: {
  children: ReactNode; palette?: Palette; title?: string;
}) {
  return (
    <main className={`min-h-dvh bg-gradient-to-br ${PALETTES[palette]} flex flex-col items-center px-4 py-6`}>
      {title ? (
        <h1 className="mb-4 text-center text-4xl font-black tracking-tight text-slate-800 drop-shadow-sm">
          {title}
        </h1>
      ) : null}
      <div className="flex w-full max-w-2xl flex-1 flex-col items-stretch gap-6">{children}</div>
    </main>
  );
}
