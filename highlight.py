"""Wrap AI keywords in <mark> tags for display (HTML-escaped, XSS-safe)."""

import re

from markupsafe import Markup, escape

from ai_filter import CN_KEYWORDS

# Chinese: keyword substrings (longest first) plus the standalone "AI" token.
_CN_RE = re.compile(
    "|".join([re.escape(k) for k in sorted(CN_KEYWORDS, key=len, reverse=True)])
    + r"|(?<![A-Za-z])AI(?![A-Za-z])"
)

# English equivalents, longest phrases first so alternation prefers them.
_EN_TERMS = [
    "artificial intelligence",
    "large language models",
    "large language model",
    "large models",
    "large model",
    "machine learning",
    "deep learning",
    "neural network",
    "embodied intelligence",
    "intelligent agents",
    "intelligent agent",
    "computing power",
    "AIGC",
    "ChatGPT",
    "AI",
]
_EN_RE = re.compile(r"\b(?:" + "|".join(re.escape(t) for t in _EN_TERMS) + r")\b", re.IGNORECASE)


def _mark(text: str, regex: re.Pattern) -> Markup:
    safe = str(escape(text or ""))
    return Markup(regex.sub(lambda m: f"<mark>{m.group(0)}</mark>", safe))


def highlight_cn(text: str) -> Markup:
    return _mark(text, _CN_RE)


def highlight_en(text: str) -> Markup:
    return _mark(text or "", _EN_RE)
