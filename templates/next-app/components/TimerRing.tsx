// Prebuilt kit — extend, don't gut.

/** Circular countdown: green -> amber -> rose as time runs out. Pure SVG, no timers inside. */
export function TimerRing({ remaining, total, size = 96 }: { remaining: number; total: number; size?: number }) {
  const r = 40;
  const c = 2 * Math.PI * r;
  const frac = total > 0 ? Math.max(0, Math.min(1, remaining / total)) : 0;
  const color = frac > 0.5 ? "#10b981" : frac > 0.2 ? "#f59e0b" : "#f43f5e";
  return (
    <svg width={size} height={size} viewBox="0 0 100 100" role="timer" aria-label={`${remaining} seconds left`}>
      <circle cx="50" cy="50" r={r} fill="none" stroke="#e2e8f0" strokeWidth="10" />
      <circle cx="50" cy="50" r={r} fill="none" stroke={color} strokeWidth="10" strokeLinecap="round"
        strokeDasharray={c} strokeDashoffset={c * (1 - frac)} transform="rotate(-90 50 50)"
        style={{ transition: "stroke-dashoffset 1s linear, stroke 0.5s" }} />
      <text x="50" y="58" textAnchor="middle" fontSize="26" fontWeight="800" fill="#334155">{Math.ceil(remaining)}</text>
    </svg>
  );
}
