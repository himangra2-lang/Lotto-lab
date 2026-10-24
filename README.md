# Lotto Lab

A phone-installable app (PWA) for Lotto Max, Lotto 6/49, BC/49 and Daily Grand:

- **Tests** – seven randomness tests on the real draw history, a frequency chart, and a backtest of "hot", "cold" and "overdue" strategies against pure chance. Each test is also run on a simulated fair machine for comparison.
- **Value** – expected payout per ticket for the next draw (jackpot, MaxPlus prizes, jackpot sharing, break-even jackpot), with exact odds for every tier.
- **Picker** – random lines that skip popular patterns (birthdays, runs, past winners) so a win is less likely to be split; plus a checker for your own numbers.
- **Ledger** – tickets, winnings and a monthly spending cap, stored on your device.

History is split by format era (Lotto Max was 7/49, then 7/50 from May 2019, then 7/52 from April 14, 2026) so statistics never mix games with different odds.

## How it runs

```
GitHub Action (daily) ──> fetcher/fetch.py ──> docs/data/*.json ──> GitHub Pages ──> app on your phone
```

Nothing runs on your phone or computer. The Action fetches new draws every day and commits them; Pages serves the app and the data from the same site.

## One-time setup (about 10 minutes, easiest on a computer)

1. Create a new **public** GitHub repository named `lotto-lab` (Pages is free for public repos).
2. Upload everything in this folder, keeping the structure (`.github/`, `docs/`, `fetcher/`, `src/`, `build.py`).
   With the GitHub CLI instead: `git init && git add . && git commit -m "Lotto Lab" && gh repo create lotto-lab --public --source . --push`
3. **Settings → Pages**: Source "Deploy from a branch", branch `main`, folder `/docs`. Save.
4. **Settings → Actions → General → Workflow permissions**: choose "Read and write permissions".
5. **Actions → Fetch draw results → Run workflow**, tick "Rebuild full history". The first run backfills every game (about 10–15 minutes).
6. Open `https://<your-username>.github.io/lotto-lab/` on your phone and choose **Add to Home Screen**.

## Notes

- Draw history comes from lotteryextreme.com's public monthly archives (no official CSV download exists). The scraper reads the site's own "previous month" links and validates every draw (right count, no duplicates, numbers within the pool for that date). If the site changes its layout, the Action log will show skipped or missing months; the official source to check against is BCLC's past winning numbers.
- You can also import draws in the app: CSV, one draw per line, `date,n1,...,nk,bonus`.
- Prize amounts for non-jackpot tiers are typical payouts and editable in the app. Odds are exact.
- Edit the app in `src/app.html`, then run `python build.py` to regenerate `docs/`.

## Run the fetcher yourself (optional)

```
pip install -r fetcher/requirements.txt
python fetcher/fetch.py            # incremental
python fetcher/fetch.py --full     # rebuild everything
```
