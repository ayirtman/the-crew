// Prebuilt kit — extend, don't gut.
export type TileState = "idle" | "selected" | "correct" | "wrong";

const STATES: Record<TileState, string> = {
  idle: "border-transparent bg-white/95 hover:border-indigo-300",
  selected: "border-indigo-400 bg-indigo-50",
  correct: "border-emerald-500 bg-emerald-50 kit-pop",
  wrong: "border-rose-400 bg-rose-50 kit-shake",
};

/** A tappable picture choice. State drives border, ground and motion. */
export function ImageTile({ src, alt, label, state = "idle", onSelect, disabled = false }: {
  src: string; alt: string; label?: string; state?: TileState;
  onSelect?: () => void; disabled?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      disabled={disabled}
      aria-label={alt}
      className={`flex min-h-44 min-w-44 flex-1 flex-col items-center justify-center gap-2 rounded-3xl
        border-8 p-4 shadow-lg transition active:scale-95 disabled:active:scale-100 ${STATES[state]}`}
    >
      {/* eslint-disable-next-line @next/next/no-img-element -- local svg assets, no optimizer */}
      <img src={src} alt="" className="h-32 w-32 object-contain" draggable={false} />
      {label ? <span className="text-2xl font-extrabold text-slate-700">{label}</span> : null}
    </button>
  );
}
