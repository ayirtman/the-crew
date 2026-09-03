// Prebuilt kit — extend, don't gut.

/** Parent-facing numbers: labeled stats in a tidy grid, tabular digits. */
export function StatPanel({ stats }: { stats: { label: string; value: string }[] }) {
  return (
    <dl className="grid grid-cols-2 gap-4 sm:grid-cols-3">
      {stats.map((s) => (
        <div key={s.label} className="rounded-2xl bg-slate-50 p-4 text-center">
          <dt className="text-sm font-semibold uppercase tracking-wide text-slate-500">{s.label}</dt>
          <dd className="mt-1 text-3xl font-black text-slate-800 tabular-nums">{s.value}</dd>
        </div>
      ))}
    </dl>
  );
}
