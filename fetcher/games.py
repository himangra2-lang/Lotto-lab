"""Game definitions shared by the fetcher.

Each game knows how many main numbers are drawn, the size of the number pool
(which can change over time: a "format era"), and what the extra number is.
"""
from datetime import date


def _lottomax_days(d):
    # Friday only until Tuesday draws were added on 2019-05-14
    return {1, 4} if d >= date(2019, 5, 14) else {4}

GAMES = {
    "lottomax": {
        "name": "Lotto Max",
        "url": "https://www.lotteryextreme.com/canada/lottomax-results",
        "main": 7,
        "bonus": "bonus",          # bonus drawn from the same pool
        "start": date(2009, 9, 25),
        "backfill_from": date(2009, 9, 25),
        "weekdays": _lottomax_days,
        # (first draw date of era, pool size)
        "eras": [
            (date(2009, 9, 25), 49),
            (date(2019, 5, 14), 50),
            (date(2026, 4, 14), 52),
        ],
    },
    "lotto649": {
        "name": "Lotto 6/49",
        "url": "https://www.lotteryextreme.com/canada/lotto649-results",
        "main": 6,
        "bonus": "bonus",
        "start": date(1982, 6, 12),
        "backfill_from": date(2010, 1, 1),
        "weekdays": lambda d: {2, 5},
        "eras": [(date(1982, 6, 12), 49)],
    },
    "bc49": {
        "name": "BC/49",
        "url": "https://www.lotteryextreme.com/canada/bc49-results",
        "main": 6,
        "bonus": "bonus",
        "start": date(1985, 1, 1),
        "backfill_from": date(2010, 1, 1),
        "weekdays": lambda d: {2, 5},
        # The results page has no per-draw links, so the fetcher probes these
        # address patterns on a known draw and keeps the first that works.
        "draw_url_candidates": [
            "https://www.lotteryextreme.com/canada/bc49({d})",
            "https://www.lotteryextreme.com/canada/bc49_numbers({d})",
            "https://www.lotteryextreme.com/canada/bc49-numbers({d})",
            "https://www.lotteryextreme.com/canada/bc49-results({d})",
            "https://www.lotteryextreme.com/canada/bc49_results({d})",
        ],
        "eras": [(date(1985, 1, 1), 49)],
    },
    "dailygrand": {
        "name": "Daily Grand",
        "url": "https://www.lotteryextreme.com/canada/dailygrand-winningnumbers",
        "main": 5,
        "bonus": "grand",          # Grand Number, drawn from 1-7
        "bonus_pool": 7,
        "start": date(2016, 10, 20),
        "backfill_from": date(2016, 10, 20),
        "weekdays": lambda d: {0, 3},
        "eras": [(date(2016, 10, 20), 49)],
    },
}


def pool_for(game: dict, d: date) -> int:
    pool = game["eras"][0][1]
    for start, size in game["eras"]:
        if d >= start:
            pool = size
    return pool


def era_id(game: dict, d: date) -> str:
    """Stable label such as '7/50' for grouping statistics by format."""
    return f'{game["main"]}/{pool_for(game, d)}'
