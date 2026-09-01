"""One-time asset pack generator for templates/next-app/public/assets.

Images: Twemoji SVGs (CC-BY 4.0) fetched from jsdelivr. Audio: Google TTS via gTTS.
Non-commercial placeholders, chosen by Batu on 2026-09-01. Needs network; run rarely.
"""
from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

from gtts import gTTS

LANGS = ["en", "de", "tr", "es", "fr"]
LANG_NAMES = {"en": "English", "de": "Deutsch", "tr": "Türkçe", "es": "Español", "fr": "Français"}

# id, emoji, en, de, tr, es, fr
WORDS = [
    ("dog", "🐶", "dog", "Hund", "köpek", "perro", "chien"),
    ("cat", "🐱", "cat", "Katze", "kedi", "gato", "chat"),
    ("ball", "⚽", "ball", "Ball", "top", "pelota", "ballon"),
    ("car", "🚗", "car", "Auto", "araba", "coche", "voiture"),
    ("apple", "🍎", "apple", "Apfel", "elma", "manzana", "pomme"),
    ("banana", "🍌", "banana", "Banane", "muz", "plátano", "banane"),
    ("bird", "🐦", "bird", "Vogel", "kuş", "pájaro", "oiseau"),
    ("fish", "🐟", "fish", "Fisch", "balık", "pez", "poisson"),
    ("cow", "🐮", "cow", "Kuh", "inek", "vaca", "vache"),
    ("horse", "🐴", "horse", "Pferd", "at", "caballo", "cheval"),
    ("duck", "🦆", "duck", "Ente", "ördek", "pato", "canard"),
    ("bear", "🐻", "bear", "Bär", "ayı", "oso", "ours"),
    ("book", "📖", "book", "Buch", "kitap", "libro", "livre"),
    ("cup", "☕", "cup", "Tasse", "fincan", "taza", "tasse"),
    ("shoe", "👟", "shoe", "Schuh", "ayakkabı", "zapato", "chaussure"),
    ("flower", "🌸", "flower", "Blume", "çiçek", "flor", "fleur"),
    ("tree", "🌳", "tree", "Baum", "ağaç", "árbol", "arbre"),
    ("sun", "☀️", "sun", "Sonne", "güneş", "sol", "soleil"),
    ("moon", "🌙", "moon", "Mond", "ay", "luna", "lune"),
    ("star", "⭐", "star", "Stern", "yıldız", "estrella", "étoile"),
]

ROOT = Path(__file__).resolve().parents[1] / "templates" / "next-app" / "public" / "assets"


def emoji_codepoints(emoji: str) -> str:
    return "-".join(f"{ord(c):x}" for c in emoji if ord(c) != 0xFE0F)


def main() -> None:
    (ROOT / "images").mkdir(parents=True, exist_ok=True)
    for lang in LANGS:
        (ROOT / "audio" / lang).mkdir(parents=True, exist_ok=True)
    manifest = {"languages": LANGS, "language_names": LANG_NAMES, "words": []}
    for wid, emoji, *translations in WORDS:
        words = dict(zip(LANGS, translations))
        img = ROOT / "images" / f"{wid}.svg"
        if not img.exists():
            url = f"https://cdn.jsdelivr.net/gh/jdecked/twemoji@15.1.0/assets/svg/{emoji_codepoints(emoji)}.svg"
            urllib.request.urlretrieve(url, img)
            print("image", wid)
        audio = {}
        for lang in LANGS:
            mp3 = ROOT / "audio" / lang / f"{wid}.mp3"
            if not mp3.exists():
                gTTS(text=words[lang], lang=lang).save(str(mp3))
                time.sleep(0.4)
                print("audio", lang, words[lang])
            audio[lang] = f"/assets/audio/{lang}/{wid}.mp3"
        manifest["words"].append({"id": wid, "image": f"/assets/images/{wid}.svg", "word": words, "audio": audio})
    (ROOT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print("manifest:", len(manifest["words"]), "words,", len(LANGS), "languages")


if __name__ == "__main__":
    main()
