// Prebuilt kit — extend, don't gut.

/** Round-by-round progress without numbers: filled dots up to `value` of `total`. */
export function ProgressDots({ value, total }: { value: number; total: number }) {
  return (
    <div className="flex justify-center gap-2" role="progressbar" aria-valuenow={value} aria-valuemin={0} aria-valuemax={total}>
      {Array.from({ length: total }, (_, i) => (
        <span key={i} className={`h-4 w-4 rounded-full transition ${i < value ? "bg-indigo-600" : "bg-slate-300"}`} />
      ))}
    </div>
  );
}
