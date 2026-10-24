"""Lotto Lab data fetcher.

Two sources on the same site:
  1. Each game's results page (latest ~20 draws)  -> keeps data current every day.
  2. One page per past draw                       -> backfills history in polite
     daily batches (MAX_PAGES_PER_RUN), resuming where the last run stopped.

Writes docs/data/<game>.json for the app, and docs/data/fetch-log.txt so problems
are visible without opening GitHub Actions logs.

Usage:
    python fetcher/fetch.py                  # update + continue backfill
    python fetcher/fetch.py --game bc49      # one game
    python fetcher/fetch.py --max-pages 50   # smaller backfill batch
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent))
from games import GAMES, era_id, pool_for  # noqa: E402

DATA_DIR = Path(__file__).resolve().parent.parent / "docs" / "data"
HEADERS = {"User-Agent": "LottoLab/1.0 (personal statistics project; about one request per second)"}
DELAY_S = 1.0
MAX_PAGES_PER_RUN = 1000

ISO_DAY_RE = re.compile(r"\((\d{4}-\d{2}-\d{2})\s+\w{3}\)")     # (2026-10-23 Fri)
US_DATE_RE = re.compile(r"\((\d{2})/(\d{2})/(\d{4})\)")          # (10/21/2026)
ISO_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
HREF_DATE_RE = re.compile(r"(?:\(|%28)(\d{4}-\d{2}-\d{2})(?:\)|%29)")

LOG: list[str] = []


def log(msg: str) -> None:
    print(msg)
    LOG.append(msg)


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


def tokens_of(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "select", "option"]):
        tag.decompose()
    return [t.strip() for t in soup.get_text("\n").split("\n") if t.strip()]


def row_date(tok: str) -> date | None:
    m = ISO_DAY_RE.search(tok)
    if m:
        return date.fromisoformat(m.group(1))
    m = US_DATE_RE.search(tok)
    if m:
        mo, d, y = map(int, m.groups())
        return date(y, mo, d)
    return None


def read_numbers(tokens: list[str], start: int, want: int, skip_words: int = 4) -> list[int]:
    """Collect up to `want` integers after `start`, allowing a few label tokens first."""
    nums: list[int] = []
    words = 0
    for tok in tokens[start:]:
        if row_date(tok):
            break
        t = tok.rstrip(":")
        if t.isdigit() and len(t) <= 2:
            nums.append(int(t))
            if len(nums) == want:
                break
        elif nums:
            break
        else:
            words += 1
            if words > skip_words:
                break
    return nums


def build_draw(d: date, nums: list[int], game: dict) -> dict | None:
    k, pool = game["main"], pool_for(game, d)
    if len(nums) < k:
        return None
    main = nums[:k]
    if len(set(main)) != k or not all(1 <= n <= pool for n in main):
        log(f"  ? {game['name']} {d}: rejected {nums}")
        return None
    extra = nums[k] if len(nums) > k else None
    if extra is not None and not 1 <= extra <= game.get("bonus_pool", pool):
        extra = None
    return {"date": d.isoformat(), "main": sorted(main), "bonus": extra, "era": era_id(game, d)}


def parse_results_page(html: str, game: dict) -> list[dict]:
    tokens = tokens_of(html)
    draws = []
    for i, tok in enumerate(tokens):
        d = row_date(tok)
        if d:
            draw = build_draw(d, read_numbers(tokens, i + 1, game["main"] + 1), game)
            if draw:
                draws.append(draw)
    return draws


def parse_draw_page(html: str, game: dict, d: date) -> dict | None:
    """Per-draw page: the header line holds the date, the numbers follow."""
    tokens = tokens_of(html)
    iso = d.isoformat()
    for i, tok in enumerate(tokens):
        if iso in tok:
            draw = build_draw(d, read_numbers(tokens, i + 1, game["main"] + 1), game)
            if draw:
                return draw
    return None


def draw_page_template(results_html: str, game: dict) -> str | None:
    """Find a per-draw link on the results page and turn it into a URL template."""
    soup = BeautifulSoup(results_html, "html.parser")
    for a in soup.find_all("a", href=True):
        m = HREF_DATE_RE.search(a["href"])
        if m and "video" not in a["href"]:
            return urljoin(game["url"], a["href"]).replace(m.group(1), "{d}")
    return game.get("draw_url")


def listed_dates(html: str) -> list[date]:
    """Per-draw pages carry a date picker listing every draw."""
    soup = BeautifulSoup(html, "html.parser")
    found = set()
    for opt in soup.find_all("option"):
        for s in (opt.get("value", ""), opt.get_text()):
            m = ISO_RE.search(s)
            if m:
                found.add(date.fromisoformat(m.group(1)))
    return sorted(found)


def schedule_dates(game: dict, start: date, end: date) -> list[date]:
    out, d = [], start
    while d <= end:
        if d.weekday() in game["weekdays"](d):
            out.append(d)
        d += timedelta(days=1)
    return out


def update_game(key: str, budget: int) -> int:
    game = GAMES[key]
    session = requests.Session()
    draws = load(key)
    before = len(draws)

    html = get(session, game["url"])
    if not html:
        log(f"{game['name']}: results page unavailable")
        return budget
    latest = parse_results_page(html, game)
    for d in latest:
        draws[d["date"]] = d
    log(f"{game['name']}: results page gave {len(latest)} draws")

    template = draw_page_template(html, game)
    if template and budget > 0:
        newest = max((date.fromisoformat(d) for d in draws), default=date.today())
        time.sleep(DELAY_S)
        page = get(session, template.format(d=newest.isoformat()))
        dates = listed_dates(page) if page else []
        source = "date list"
        if len(dates) < 50:
            dates = schedule_dates(game, game["backfill_from"], newest)
            source = "draw schedule"
        wanted = [d for d in dates if d >= game["backfill_from"] and d.isoformat() not in draws
                  and d.isoformat() not in FAILED.get(key, [])]
        wanted.sort(reverse=True)          # newest first: recent history matters most
        log(f"{game['name']}: {len(wanted)} past draws missing ({source}); fetching up to {budget}")
        got = misses = 0
        for d in wanted[:budget]:
            time.sleep(DELAY_S)
            budget -= 1
            page = get(session, template.format(d=d.isoformat()))
            draw = parse_draw_page(page, game, d) if page else None
            if draw:
                draws[draw["date"]] = draw
                got += 1
                misses = 0
            else:
                FAILED.setdefault(key, []).append(d.isoformat())
                misses += 1
                if misses >= 25 and got == 0:
                    log(f"{game['name']}: 25 draw pages in a row failed to parse; stopping backfill")
                    break
        log(f"{game['name']}: backfilled {got} draws")
    elif not template:
        log(f"{game['name']}: no per-draw page link found; only keeping current draws")

    save(key, draws)
    log(f"{game['name']}: {before} -> {len(draws)} draws saved")
    return budget


def load(key: str) -> dict[str, dict]:
    path = DATA_DIR / f"{key}.json"
    if not path.exists():
        return {}
    return {d["date"]: d for d in json.loads(path.read_text()).get("draws", [])}


def save(key: str, draws: dict[str, dict]) -> None:
    game = GAMES[key]
    ordered = sorted(draws.values(), key=lambda d: d["date"])
    out = {
        "game": key, "name": game["name"], "main": game["main"], "bonusType": game["bonus"],
        "eras": [{"from": s.isoformat(), "pool": p} for s, p in game["eras"]],
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "count": len(ordered), "draws": ordered,
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / f"{key}.json").write_text(json.dumps(out, separators=(",", ":")))


FAILED_PATH = DATA_DIR / "failed-dates.json"
FAILED: dict[str, list[str]] = {}


def main() -> int:
    global FAILED
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", choices=list(GAMES), action="append")
    ap.add_argument("--max-pages", type=int, default=MAX_PAGES_PER_RUN)
    ap.add_argument("--retry-failed", action="store_true", help="retry dates that failed before")
    args = ap.parse_args()
    if FAILED_PATH.exists() and not args.retry_failed:
        FAILED = json.loads(FAILED_PATH.read_text())
    keys = args.game or list(GAMES)
    share = max(1, args.max_pages // len(keys))
    for key in keys:
        update_game(key, share)
    FAILED_PATH.write_text(json.dumps(FAILED))
    (DATA_DIR / "index.json").write_text(json.dumps({k: {"name": g["name"]} for k, g in GAMES.items()}))
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    (DATA_DIR / "fetch-log.txt").write_text(f"Run {stamp}\n" + "\n".join(LOG) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
