"""人民日报 reader — page-1 overview + AI mentions, navigable by date."""

import glob
import os
from datetime import datetime, timedelta

from flask import Flask, abort, redirect, render_template, url_for

from scraper import DATA_DIR, load_edition
from ai_filter import ai_articles
from translate import load_translations
from highlight import highlight_cn, highlight_en

app = Flask(__name__)

DATE_FMT = "%Y-%m-%d"


def available_dates() -> list[str]:
    """Dates that have a cached edition (and therefore a built page)."""
    files = glob.glob(os.path.join(DATA_DIR, "edition_*.json"))
    return sorted(os.path.basename(f)[len("edition_"):-len(".json")] for f in files)


def parse_date(date_str: str) -> datetime:
    try:
        return datetime.strptime(date_str, DATE_FMT)
    except ValueError:
        abort(404)


def build_view(date_str: str) -> dict:
    articles = load_edition(date_str)
    table = load_translations(date_str)

    def en(text):
        return table.get((text or "").strip())

    def snippets_of(a):
        return [
            {"cn": highlight_cn(s), "en": highlight_en(e) if (e := en(s)) else None}
            for s in a["ai_snippets"]
        ]

    page1_src = [a for a in articles if a.get("page") == 1]
    p1_ai = {a["url"]: a for a in ai_articles(page1_src)}
    page1 = []
    for a in page1_src:
        match = p1_ai.get(a["url"])
        title_en = en(a["title"])
        page1.append({
            "title": highlight_cn(a["title"]) if match else a["title"],
            "title_en": (highlight_en(title_en) if match else title_en) if title_en else None,
            "url": a["url"],
            "has_ai": bool(match),
            "snippets": snippets_of(match) if match else [],
        })

    other_ai = []
    for a in ai_articles(articles):
        if a.get("page", 0) < 2:
            continue
        title_en = en(a["title"])
        other_ai.append({
            "title": highlight_cn(a["title"]),
            "title_en": highlight_en(title_en) if title_en else None,
            "url": a["url"],
            "page": a.get("page"),
            "snippets": snippets_of(a),
        })

    d = parse_date(date_str)
    return {
        "date": date_str,
        "date_display": d.strftime("%A, %B %-d, %Y"),
        "prev_date": (d - timedelta(days=1)).strftime(DATE_FMT),
        "next_date": (d + timedelta(days=1)).strftime(DATE_FMT),
        "is_future": d.date() >= datetime.now().date(),
        # URL shape — overridden by the static builder for flat .html output
        "static_href": "/static/style.css",
        "day_url_prefix": "/day/",
        "day_url_suffix": "",
        "available_dates": available_dates(),
        "has_data": bool(articles),
        "page1": page1,
        "p1_ai_count": len(p1_ai),
        "other_ai": other_ai,
        "total_articles": len(articles),
    }


@app.route("/")
def home():
    return redirect(url_for("day", date_str=datetime.now().strftime(DATE_FMT)))


@app.route("/day/<date_str>")
def day(date_str):
    parse_date(date_str)
    return render_template("index.html", **build_view(date_str))


if __name__ == "__main__":
    app.run(debug=True, port=5050)
