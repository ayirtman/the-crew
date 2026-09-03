# Design corpus

The app is composed from the prebuilt kit in `components/`. Import them (`import { AppShell } from "@/components/AppShell"`) and compose; extend, don't gut. Tailwind is available for layout glue only — the look lives in the kit.

- `AppShell` — the stage: full-height gradient ground (`palette`: sky | sunset | meadow | berry), centered column, optional `title`. Every screen sits inside one.
- `Card` — one concern per card: white rounded surface with a soft shadow.
- `BigButton` — the tap target, ≥64px tall (`variant`: primary | success | ghost | danger, `full` for full-width). One primary per screen.
- `ImageTile` — a tappable picture choice (`state`: idle | selected | correct | wrong). Correct pops, wrong shakes; state drives border and ground.
- `ResultBanner` — big feedback strip, `role="status"` (`variant`: success | error | info).
- `ProgressDots` — round-by-round progress without numbers.
- `LanguagePicker` — big flag buttons for en, de, tr, es, fr.
- `TimerRing` — circular countdown SVG; green → amber → rose. Pass `remaining`/`total`; keep the interval in the page.
- `StatPanel` — parent-facing numbers: labeled stats, tabular digits.

Legacy CSS classes (btn-primary, btn-ghost, card, field, result-line, image-tile) still exist in globals.css but the kit is preferred.

Rules: touch targets at least 64px; one primary action per screen; large playful type; color never carries meaning alone (pair with text, icon or motion); every interactive element visible at a glance. The app must look finished — composed from the kit, not from raw unstyled HTML.
