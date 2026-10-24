"""Lotto Lab data fetcher.

Scrapes past winning numbers month by month and writes one JSON file per game
into docs/data/, which the PWA reads. Runs incrementally: after the first full
backfill it only re-reads the most recent months.

Usage:
    python fetcher/fetch.py                 # all games, incremental
    python fetcher/fetch.py --full          # rebuild full history
    python fetcher/fetch.py --game lottomax # one game
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent))
from games import GAMES, era_id, pool_for  # noqa: E402

DATA_DIR = Path(__file__).resolve().parent.parent / "docs" / "data"
HEADERS = {"User-Agent": "LottoLab/1.0 (personal statistics project; polite monthly fetch)"}
DELAY_S = 1.2           # be polite to the source site
INCREMENTAL_MONTHS = 3  # months re-read on a normal run

DATE_RE = re.compile(r"\((\d{4}-\d{2}-\d{2})\s+\w{3}\)")
MONTH_RE = re.compile(r"(?:\(|%28)(\d{4})-(\d{2})(?:\)|%29)")


def get(session: requests.Session, url: str) -> str | None:
    for attempt in range(3):
        try:
            r = session.get(url, headers=HEADERS, timeout=30)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.text
        except requests.RequestException as e:
            print(f"  ! {url}: {e} (attempt {attempt + 1})", file=sys.stderr)
            time.sleep(3 * (attempt + 1))
    return None


def parse_draws(html: str, game: dict) -> list[dict]:
    """Find every '(YYYY-MM-DD Day)' marker, then read the integers after it."""
    soup = BeautifulSoup(html, "html.parser")
    tokens = [t.strip() for t in soup.get_text("\n").split("\n") if t.strip()]
    draws = []
    i = 0
    while i < len(tokens):
        m = DATE_RE.search(tokens[i])
        if not m:
            i += 1
            continue
        d = date.fromisoformat(m.group(1))
        nums = []
        j = i + 1
        while j < len(tokens) and len(nums) < game["main"] + 1:
            tok = tokens[j]
            if DATE_RE.search(tok):
                break
            if tok.isdigit():
                nums.append(int(tok))
            elif nums:          # numbers are contiguous; stop at the first non-number after them
                break
            j += 1
        draw = build_draw(d, nums, game)
        if draw:
            draws.append(draw)
        i = j
    return draws


def build_draw(d: date, nums: list[int], game: dict) -> dict | None:
    k, pool = game["main"], pool_for(game, d)
    if len(nums) < k:
        return None
    main = nums[:k]
    if len(set(main)) != k or not all(1 <= n <= pool for n in main):
        print(f"  ? skipped {d}: {nums}", file=sys.stderr)
        return None
    extra = nums[k] if len(nums) > k else None
    bonus_pool = game.get("bonus_pool", pool)
    if extra is not None and not 1 <= extra <= bonus_pool:
        extra = None
    return {"date": d.isoformat(), "main": sorted(main), "bonus": extra, "era": era_id(game, d)}


def previous_month_url(html: str, page_url: str, y: int, m: int, base: str) -> list[str]:
    """Prefer the site's own 'previous month' link; fall back to known URL shapes."""
    py, pm = (y - 1, 12) if m == 1 else (y, m - 1)
    soup = BeautifulSoup(html, "html.parser")
    found = []
    for a in soup.find_all("a", href=True):
        mm = MONTH_RE.search(a["href"])
        if mm and (int(mm.group(1)), int(mm.group(2))) == (py, pm):
            found.append(urljoin(page_url, a["href"]))
    found += [f"{base}({py}-{pm:02d})", f"{base}%28{py}-{pm:02d}%29"]
    return list(dict.fromkeys(found))


def crawl(game_key: str, full: bool, existing: dict[str, dict]) -> dict[str, dict]:
    game = GAMES[game_key]
    session = requests.Session()
    draws = dict(existing)
    today = date.today()
    y, m = today.year, today.month
    url = game["url"]
    html = get(session, url)
    months_done = 0
    while html:
        page = parse_draws(html, game)
        new = [d for d in page if d["date"] not in draws]
        for d in page:
            draws[d["date"]] = d
        print(f"  {game_key} {y}-{m:02d}: {len(page)} draws ({len(new)} new)")
        months_done += 1
        if not full and months_done >= INCREMENTAL_MONTHS and existing:
            break
        if date(y, m, 1) <= game["start"].replace(day=1):
            break
        candidates = previous_month_url(html, url, y, m, game["url"])
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
        html = None
        for cand in candidates:
            time.sleep(DELAY_S)
            html = get(session, cand)
            if html and parse_draws(html, game):
                url = cand
                break
            html = None
        if html is None:
            print(f"  {game_key}: no archive page found for {y}-{m:02d}; stopping")
    return draws


def load(game_key: str) -> dict[str, dict]:
    path = DATA_DIR / f"{game_key}.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    return {d["date"]: d for d in data.get("draws", [])}


def save(game_key: str, draws: dict[str, dict]) -> None:
    game = GAMES[game_key]
    ordered = sorted(draws.values(), key=lambda d: d["date"])
    out = {
        "game": game_key,
        "name": game["name"],
        "main": game["main"],
        "bonusType": game["bonus"],
        "eras": [{"from": s.isoformat(), "pool": p} for s, p in game["eras"]],
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "count": len(ordered),
        "draws": ordered,
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / f"{game_key}.json").write_text(json.dumps(out, separators=(",", ":")))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="re-crawl full history")
    ap.add_argument("--game", choices=list(GAMES), action="append")
    args = ap.parse_args()
    failures = 0
    for key in args.game or list(GAMES):
        existing = {} if args.full else load(key)
        # Small files mean only seed data: do a full backfill automatically.
        full = args.full or len(existing) < 100
        print(f"== {GAMES[key]['name']} ({'full' if full else 'incremental'}) ==")
        draws = crawl(key, full, existing)
        if len(draws) <= len(existing) and not existing:
            failures += 1
            print(f"  ! no data for {key}", file=sys.stderr)
            continue
        save(key, draws)
        print(f"  saved {len(draws)} draws")
    index = {k: {"name": g["name"]} for k, g in GAMES.items()}
    (DATA_DIR / "index.json").write_text(json.dumps(index))
    return 1 if failures == len(args.game or GAMES) else 0


if __name__ == "__main__":
    sys.exit(main())
