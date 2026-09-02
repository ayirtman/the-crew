# Design corpus

The only components a design or a page may use. Classes live in app/globals.css.

- Spacing scale: 4, 8, 16, 24, 32px. No other margins or paddings.
- `.btn-primary` — the one main action per screen. Large: min-height 64px, full-width on small screens, 20px text.
- `.btn-ghost` — secondary actions (skip, back, settings). Transparent, bordered.
- `.card` — a white rounded container with 16px padding and a soft shadow; groups one concern.
- `.field` — a label above an input, 16px gap below; inputs min-height 56px.
- `.result-line` — a single prominent line for the answer or feedback; 24px text, role="status".
- `.image-tile` — a tappable square image container, min 160x160px, rounded, 4px border that can signal selected/correct/wrong.

Rules: touch targets at least 64px in the smallest dimension; one primary action per screen; playful large type (min 18px body) because small children and hurried adults are the users; color signals never carry meaning alone (pair with text or icon).
