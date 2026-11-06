"""Scrape one date's full edition and auto-translate new strings via the OpenCode Go API.

Run for today (default) or a given date:
    python daily_update.py                # today
    python daily_update.py 2026-05-30     # a specific date
    python daily_update.py 2026-05-30 --dry-run   # report only, no scrape/API

Writes data/edition_<date>.json (via the scraper) and merges new English
translations into data/translations_<date>.json. Idempotent: already-translated
strings are skipped, so re-running a date only fills gaps.

Requires OPENCODE_API_KEY in the environment for the live (non-dry-run) path.
"""

import json
import os
import re
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from scraper import DATA_DIR, get_edition, load_edition
from ai_filter import ai_articles

# GLM 5.3 Flash is plenty for short zh→en news translation — cheap and fast.
# Override with TRANSLATE_MODEL (e.g. "glm-5.3") for more nuance.
MODEL = os.environ.get("TRANSLATE_MODEL", "glm-5.3-flash")
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

# OpenAI-style structured output (supported on OpenCode Go's GLM models).
RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {"name": "translations", "strict": True, "schema": OUTPUT_SCHEMA},
}

# Flipped off on the first "model doesn't support response_format" rejection;
# the run then relies on the prompt's JSON instructions instead.
_use_response_format = True


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


def _extract_json(text: str) -> dict:
    """Parse a JSON object out of a model reply, tolerating markdown fences."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[\w-]*\s*", "", text)
        if text.endswith("```"):
            text = text[:-3]
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        text = text[start : end + 1]
    return json.loads(text)


def _translate_call(client, items: list[dict]) -> dict[int, str]:
    """One API call. items=[{id, zh}]. Returns {id: english}, mapped by id."""
    global _use_response_format
    import openai  # lazy: only needed on the live path

    user = (
        'Translate the "zh" field of each item below into English. Return JSON '
        '{"translations": [{"id": <same id>, "en": "<translation>"}, ...]} — exactly one '
        "object per input item, echoing its id. Translate every item.\n\n"
        + json.dumps(items, ensure_ascii=False, indent=2)
    )
    kwargs: dict = {
        "model": MODEL,
        "max_tokens": 8000,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ],
    }
    if _use_response_format:
        kwargs["response_format"] = RESPONSE_FORMAT
    try:
        resp = client.chat.completions.create(**kwargs)
    except openai.BadRequestError:
        if "response_format" not in kwargs:  # unrelated bad request — let it raise
            raise
        _use_response_format = False  # model rejects schemas; retry without
        del kwargs["response_format"]
        resp = client.chat.completions.create(**kwargs)
    data = _extract_json(resp.choices[0].message.content)["translations"]
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

    import openai  # lazy: only needed on the live path
    import uuid

    client = openai.OpenAI(
        api_key=os.environ["OPENCODE_API_KEY"],
        base_url="https://opencode.ai/inference/go/openai/v1",
        # OpenCode Go requires callers to self-identify (see
        # https://opencode.ai/docs/go): a stable session header per
        # conversation/run, and a descriptive user agent.
        default_headers={
            "User-Agent": "rmrb-dashboard/1.0",
            "x-opencode-session": str(uuid.uuid4()),
        },
    )
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
