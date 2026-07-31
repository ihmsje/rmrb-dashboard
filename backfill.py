"""Backfill scraped + translated editions across a date range.

Meant to be run from the "Backfill archive" GitHub Actions workflow, but also
runs locally if you have ANTHROPIC_API_KEY set:

    python backfill.py 2026-01-01 2026-05-20
    python backfill.py 2026-01-01 2026-05-20 --refresh    # re-scrape dates that already have data
    python backfill.py 2026-01-01 2026-05-20 --no-commit  # write data but skip git commit/push

Commits and pushes one date at a time (like the daily job), so a run that
gets killed partway (e.g. GitHub's 6-hour job limit) loses at most one date
of progress. Re-running the same range afterwards skips dates that already
have cached data, so it picks up right where it left off.
"""

import argparse
import os
import subprocess
import sys
from datetime import datetime, timedelta

from daily_update import main as run_date
from scraper import cache_file

DATE_FMT = "%Y-%m-%d"


def date_range(start: str, end: str) -> list[str]:
    d = datetime.strptime(start, DATE_FMT)
    e = datetime.strptime(end, DATE_FMT)
    if e < d:
        raise ValueError(f"end date {end} is before start date {start}")
    out = []
    while d <= e:
        out.append(d.strftime(DATE_FMT))
        d += timedelta(days=1)
    return out


def commit_and_push(date_str: str) -> bool:
    """Commit data/ changes for one date, if any. Returns True if a commit was made."""
    subprocess.run(["git", "add", "data/"], check=True)
    diff = subprocess.run(["git", "diff", "--cached", "--quiet"])
    if diff.returncode == 0:
        return False
    subprocess.run(["git", "commit", "-m", f"Backfill: {date_str}"], check=True)
    subprocess.run(["git", "push"], check=True)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("start", help="First date to backfill (YYYY-MM-DD)")
    parser.add_argument("end", help="Last date to backfill (YYYY-MM-DD), inclusive")
    parser.add_argument("--refresh", action="store_true", help="Re-scrape dates that already have cached data")
    parser.add_argument("--no-commit", action="store_true", help="Write data files but skip git commit/push")
    args = parser.parse_args()

    dates = date_range(args.start, args.end)
    print(f"Backfilling {len(dates)} date(s): {dates[0]} .. {dates[-1]}")

    for i, date_str in enumerate(dates, 1):
        if os.path.exists(cache_file(date_str)) and not args.refresh:
            print(f"[{i}/{len(dates)}] {date_str}: already cached, skipping (use --refresh to force).")
            continue

        print(f"[{i}/{len(dates)}] {date_str}: scraping + translating...")
        try:
            run_date(date_str, dry_run=False)
        except Exception as e:  # noqa: BLE001 — one bad date must not kill the whole backfill
            print(f"[{i}/{len(dates)}] {date_str}: FAILED — {e}")
            continue

        if not args.no_commit:
            committed = commit_and_push(date_str)
            print(f"[{i}/{len(dates)}] {date_str}: {'committed + pushed' if committed else 'no changes to commit'}")

    print("Backfill complete.")


if __name__ == "__main__":
    sys.exit(main())
