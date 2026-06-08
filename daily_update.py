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
from zoneinfo import ZoneInfo

from scraper import DATA_DIR, get_edition, load_edition
from ai_filter import ai_articles

# Haiku is plenty for short zh→en news translation and ~5x cheaper than Opus.
# Override with TRANSLATE_MODEL (e.g. "claude-sonnet-4-6") for more nuance.
MODEL = os.environ.get("TRANSLATE_MODEL", "claude-haiku-4-5")
BATCH_SIZE = 40
DATE_FMT = "%Y-%m-%d"
# People's Daily editions are dated by Beijing date, so "today" must be the
# date in China — not the runner's UTC date, which can lag by up to 8 hours.
BEIJING = ZoneInfo("Asia/Shanghai")

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
    "properties": {
        "translations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "en": {"type": "string"},
                },
                "required": ["id", "en"],
                "additionalProperties": False,
            },
        }
    },
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


def _translate_call(client, items: list[dict]) -> dict[int, str]:
    """One API call. items=[{id, zh}]. Returns {id: english}, mapped by id."""
    user = (
        'Translate the "zh" field of each item below into English. Return JSON '
        '{"translations": [{"id": <same id>, "en": "<translation>"}, ...]} — exactly one '
        "object per input item, echoing its id. Translate every item.\n\n"
        + json.dumps(items, ensure_ascii=False, indent=2)
    )
    resp = client.messages.create(
        model=MODEL,
        max_tokens=8000,
        system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
        output_config={"format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
    )
    text = next(b.text for b in resp.content if b.type == "text")
    data = json.loads(text)["translations"]
    return {int(o["id"]): o["en"] for o in data if isinstance(o.get("en"), str) and o["en"].strip()}


def translate_strings(client, strings: list[str]) -> dict[str, str]:
    """Translate as many strings as possible; return {chinese: english}.

    Resilient by design — a string the model keeps dropping must never abort
    the run. Strategy: batched passes (mapped by id), then a per-string
    fallback for stragglers, and finally give up on anything still missing
    (it stays Chinese-only on the site and gets retried on the next run).
    Never raises.
    """
    done: dict[int, str] = {}

    # Pass 1: batched, with a couple of retries for ids the model drops.
    for start in range(0, len(strings), BATCH_SIZE):
        idxs = range(start, min(start + BATCH_SIZE, len(strings)))
        for _ in range(2):
            pending = [{"id": i, "zh": strings[i]} for i in idxs if i not in done]
            if not pending:
                break
            try:
                done.update(_translate_call(client, pending))
            except Exception as e:  # noqa: BLE001 — keep going on any API/parse error
                print(f"  [warn] batch translate error: {e}")
        print(f"  translated {min(start + BATCH_SIZE, len(strings))}/{len(strings)}")

    # Pass 2: one-at-a-time fallback for stragglers (almost always succeeds).
    stragglers = [i for i in range(len(strings)) if i not in done]
    for i in stragglers:
        for _ in range(2):
            try:
                got = _translate_call(client, [{"id": i, "zh": strings[i]}])
                if i in got:
                    done[i] = got[i]
                    break
            except Exception as e:  # noqa: BLE001
                print(f"  [warn] single translate error (#{i}): {e}")

    missing = [i for i in range(len(strings)) if i not in done]
    if missing:
        print(f"  [warn] left {len(missing)} string(s) untranslated (Chinese-only; "
              "will retry on the next run)")

    return {strings[i]: en for i, en in done.items()}


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
    table.update(translate_strings(client, todo))

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(table, f, ensure_ascii=False, indent=2)
    print(f"{date_str}: wrote {len(table)} translations -> {path}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    date = args[0] if args else datetime.now(BEIJING).strftime(DATE_FMT)
    main(date, dry_run="--dry-run" in flags)
