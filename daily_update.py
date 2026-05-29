"""Scrape one date's full edition and auto-translate new strings via the Claude API.

Run for today (default) or a given date:
    python daily_update.py                # today
    python daily_update.py 2026-05-30     # a specific date
    python daily_update.py 2026-05-30 --dry-run   # report only, no scrape/API

Writes data/edition_<date>.json (via the scraper) and merges new English
translations into data/translations_<date>.json. Idempotent: already-translated
strings are skipped, so re-running a date only fills gaps.

Requires ANTHROPIC_API_KEY in the environment for the live (non-dry-run) path.
"""

import json
import os
import sys
from datetime import datetime

from scraper import DATA_DIR, get_edition, load_edition
from ai_filter import ai_articles

MODEL = os.environ.get("TRANSLATE_MODEL", "claude-opus-4-7")
BATCH_SIZE = 40
DATE_FMT = "%Y-%m-%d"

SYSTEM_PROMPT = (
    "You are a professional translator rendering text from China's People's Daily "
    "(人民日报) into English. Translate each Chinese string into fluent, faithful English "
    "in a news register. Keep proper nouns, official names, and quoted program/campaign "
    "names. Preserve numbers, units, and percentages. Render 人工智能 as 'artificial "
    "intelligence' (or 'AI' where the source itself writes 'AI'), 大模型 as 'large model', "
    "大语言模型 as 'large language model', 算力 as 'computing power'. Preserve parenthetical "
    "section labels such as （xxx·xxx）."
)

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {"translations": {"type": "array", "items": {"type": "string"}}},
    "required": ["translations"],
    "additionalProperties": False,
}


def translations_path(date_str: str) -> str:
    return os.path.join(DATA_DIR, f"translations_{date_str}.json")


def needed_strings(articles: list[dict]) -> list[str]:
    """Page-1 titles + AI-article titles + AI snippets, de-duplicated, in order."""
    seen: set[str] = set()
    out: list[str] = []

    def add(text: str) -> None:
        text = (text or "").strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)

    for a in articles:
        if a.get("page") == 1:
            add(a["title"])
    for a in ai_articles(articles):
        add(a["title"])
        for snippet in a["ai_snippets"]:
            add(snippet)
    return out


def translate_batch(client, strings: list[str]) -> list[str]:
    """Translate a batch, returning English strings aligned by index."""
    user = (
        'Translate each string below. Return JSON {"translations": [...]} — an array of '
        "English strings in the SAME ORDER and SAME LENGTH as the input array. Do not add, "
        "drop, or merge entries.\n\n" + json.dumps(strings, ensure_ascii=False, indent=2)
    )
    resp = client.messages.create(
        model=MODEL,
        max_tokens=8000,
        system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
        output_config={"format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
    )
    text = next(b.text for b in resp.content if b.type == "text")
    result = json.loads(text)["translations"]
    if len(result) != len(strings):
        raise ValueError(f"translation length mismatch: {len(result)} vs {len(strings)}")
    return result


def main(date_str: str, dry_run: bool = False) -> None:
    articles = load_edition(date_str) if dry_run else get_edition(date_str, refresh=True)
    if not articles:
        print(f"{date_str}: no edition available — nothing to do.")
        return

    path = translations_path(date_str)
    table = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}
    todo = [s for s in needed_strings(articles) if s not in table]
    print(f"{date_str}: {len(articles)} articles, {len(todo)} new strings to translate.")

    if dry_run or not todo:
        return

    import anthropic  # lazy: only needed on the live path

    client = anthropic.Anthropic()
    for i in range(0, len(todo), BATCH_SIZE):
        chunk = todo[i:i + BATCH_SIZE]
        for src, english in zip(chunk, translate_batch(client, chunk)):
            table[src] = english
        print(f"  translated {min(i + BATCH_SIZE, len(todo))}/{len(todo)}")

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(table, f, ensure_ascii=False, indent=2)
    print(f"{date_str}: wrote {len(table)} translations -> {path}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    date = args[0] if args else datetime.now().strftime(DATE_FMT)
    main(date, dry_run="--dry-run" in flags)
