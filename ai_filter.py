"""Detect articles mentioning AI and extract the relevant sentences."""

import re

# Chinese keywords matched as substrings; "AI" matched as a standalone token.
CN_KEYWORDS = [
    "人工智能",
    "大模型",
    "大语言模型",
    "生成式",
    "机器学习",
    "深度学习",
    "智能体",
    "神经网络",
    "算力",
    "AIGC",
    "ChatGPT",
]
_AI_LATIN = re.compile(r"(?<![A-Za-z])AI(?![A-Za-z])")
_SENT_SPLIT = re.compile(r"[。！？；\n]+")


def _matches(text: str) -> bool:
    if not text:
        return False
    if any(k in text for k in CN_KEYWORDS):
        return True
    return bool(_AI_LATIN.search(text))


def find_ai_snippets(text: str) -> list[str]:
    """Return de-duplicated sentences from `text` that mention AI."""
    snippets = []
    seen = set()
    for raw in _SENT_SPLIT.split(text or ""):
        sent = raw.strip()
        if not sent or sent in seen:
            continue
        if _matches(sent):
            seen.add(sent)
            snippets.append(sent)
    return snippets


def ai_articles(articles: list[dict]) -> list[dict]:
    """Filter articles to those mentioning AI, attaching `ai_snippets`."""
    result = []
    for art in articles:
        text = art.get("content", "")
        if not (_matches(text) or _matches(art.get("title", ""))):
            continue
        snippets = find_ai_snippets(text)
        if not snippets and _matches(art.get("title", "")):
            snippets = [art["title"]]
        enriched = dict(art)
        enriched["ai_snippets"] = snippets
        result.append(enriched)
    return result
