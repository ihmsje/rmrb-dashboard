"""Render every cached edition to flat static HTML in build/.

Output layout (host-agnostic, works on GitHub/Cloudflare Pages):
    build/index.html        -> redirect to the latest day
    build/<date>.html       -> one page per cached edition
    build/static/style.css  -> copied assets

Reuses the Flask app's templates and build_view(), overriding the URL
variables so links are flat ".html" files instead of dynamic routes.
"""

import glob
import os
import shutil

from app import app, build_view

ROOT = os.path.dirname(__file__)
DATA_DIR = os.path.join(ROOT, "data")
OUT_DIR = os.path.join(ROOT, "build")

# Static-hosting URL shape: flat files, sibling links, local assets.
STATIC_URLS = {
    "static_href": "static/style.css",
    "day_url_prefix": "",
    "day_url_suffix": ".html",
}

_REDIRECT = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta http-equiv="refresh" content="0; url=./{latest}.html">
<link rel="canonical" href="./{latest}.html">
<title>人民日报 Reader</title></head>
<body><p>Redirecting to <a href="./{latest}.html">the latest edition</a>…</p></body></html>
"""

# Served by GitHub Pages for any unknown path (e.g. a directly-typed date with
# no edition). Self-contained — no external stylesheet — and bounces to the
# latest edition after a short, readable pause.
_NOT_FOUND = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="3; url=./">
<title>人民日报 Reader — not found</title>
<style>
  body {{ margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center;
         background:#f7f5f1; color:#1a1a1a; text-align:center;
         font-family:-apple-system,"Helvetica Neue","PingFang SC",sans-serif; }}
  .box {{ max-width:420px; padding:0 24px; }}
  h1 {{ color:#b21f1f; font-size:22px; margin:0 0 10px; }}
  p {{ color:#6b6b6b; font-size:15px; line-height:1.5; margin:0 0 18px; }}
  a {{ display:inline-block; background:#b21f1f; color:#fff; text-decoration:none;
       padding:9px 18px; border-radius:6px; font-size:14px; }}
</style></head>
<body><div class="box">
  <h1>No data available for this date</h1>
  <p>That edition hasn't been published or scraped yet. Taking you to the latest edition…</p>
  <a href="./">Go to the latest edition</a>
</div></body></html>
"""


def available_dates() -> list[str]:
    files = glob.glob(os.path.join(DATA_DIR, "edition_*.json"))
    return sorted(os.path.basename(f)[len("edition_"):-len(".json")] for f in files)


def render_day(date_str: str) -> str:
    ctx = build_view(date_str)
    ctx.update(STATIC_URLS)
    return app.jinja_env.get_template("index.html").render(**ctx)


def build() -> None:
    dates = available_dates()
    if not dates:
        raise SystemExit("No cached editions found in data/ — nothing to build.")

    if os.path.exists(OUT_DIR):
        shutil.rmtree(OUT_DIR)
    os.makedirs(OUT_DIR)

    shutil.copytree(os.path.join(ROOT, "static"), os.path.join(OUT_DIR, "static"))

    for d in dates:
        with open(os.path.join(OUT_DIR, f"{d}.html"), "w", encoding="utf-8") as f:
            f.write(render_day(d))

    latest = dates[-1]
    with open(os.path.join(OUT_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(_REDIRECT.format(latest=latest))

    with open(os.path.join(OUT_DIR, "404.html"), "w", encoding="utf-8") as f:
        f.write(_NOT_FOUND)

    print(f"Built {len(dates)} pages -> {OUT_DIR} (latest: {latest})")


if __name__ == "__main__":
    build()
