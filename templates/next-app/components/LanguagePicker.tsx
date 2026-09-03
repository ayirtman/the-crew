// Prebuilt kit — extend, don't gut.

const FLAGS: Record<string, string> = { en: "🇬🇧", de: "🇩🇪", tr: "🇹🇷", es: "🇪🇸", fr: "🇫🇷" };
const NAMES: Record<string, string> = { en: "English", de: "Deutsch", tr: "Türkçe", es: "Español", fr: "Français" };

/** Big flag buttons for the five template languages. */
export function LanguagePicker({ languages, selected, onSelect }: {
  languages: string[]; selected?: string; onSelect: (lang: string) => void;
}) {
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
      {languages.map((lang) => (
        <button
          key={lang}
          type="button"
          onClick={() => onSelect(lang)}
          aria-pressed={selected === lang}
          className={`flex min-h-20 items-center justify-center gap-3 rounded-2xl border-4 px-4 text-xl font-bold transition active:scale-95
            ${selected === lang ? "border-indigo-500 bg-indigo-50 text-indigo-800" : "border-slate-200 bg-white/90 text-slate-700 hover:border-indigo-300"}`}
        >
          <span className="text-3xl" aria-hidden>{FLAGS[lang] ?? "🌍"}</span>
          {NAMES[lang] ?? lang}
        </button>
      ))}
    </div>
  );
}
