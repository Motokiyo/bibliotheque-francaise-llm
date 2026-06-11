#!/usr/bin/env python3
"""Audit local book JSON files for obvious truncation or Wikisource noise."""

from __future__ import annotations

import json
import sys
from pathlib import Path


BOOKS_DIR = Path(__file__).resolve().parents[1] / "data" / "books"
NOISE_PATTERNS = [
    "Récupérée de",
    "Ajouter des langues",
    "Rechercher",
    "Catégories",
    "Texte sur une seule page",
    "bookLes",
    "La dernière modification de cette page",
    "À propos de Wikisource",
]

EXPECTED_CHAPTERS = {
    "ile-au-tresor": 34,
    "lancelot-charrette": 21,
    "perceval-conte-du-graal": 30,
    "tristan-et-iseut": 19,
    "voyages-de-gulliver": 36,
    "yvain-chevalier-au-lion": 23,
}


def audit_book(path: Path) -> list[str]:
    errors: list[str] = []
    book = json.loads(path.read_text(encoding="utf-8"))
    book_id = book.get("id", path.stem)
    chapters = book.get("chapters") or []
    expected = EXPECTED_CHAPTERS.get(book_id)
    if expected is not None and len(chapters) != expected:
        errors.append(f"{book_id}: expected {expected} chapters, got {len(chapters)}")
    if not chapters:
        errors.append(f"{book_id}: no chapters")
        return errors

    lengths = [len((chapter.get("text") or "").strip()) for chapter in chapters]
    for idx, length in enumerate(lengths, start=1):
        if length < 500:
            title = chapters[idx - 1].get("title", "")
            errors.append(f"{book_id}: chapter {idx} too short ({length} chars): {title}")

    joined = "\n".join(chapter.get("text", "") for chapter in chapters)
    for pattern in NOISE_PATTERNS:
        if pattern in joined:
            errors.append(f"{book_id}: noise pattern found: {pattern!r}")

    seen_numbers = [chapter.get("number") for chapter in chapters]
    expected_numbers = list(range(1, len(chapters) + 1))
    if seen_numbers != expected_numbers:
        errors.append(f"{book_id}: non-contiguous chapter numbers: {seen_numbers[:8]}...")

    print(
        f"{book_id}: {len(chapters)} chapters, {sum(lengths)} chars, "
        f"min={min(lengths)}, max={max(lengths)}"
    )
    return errors


def main() -> int:
    errors: list[str] = []
    for path in sorted(BOOKS_DIR.glob("*.json")):
        errors.extend(audit_book(path))
    if errors:
        print("\nERRORS", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
