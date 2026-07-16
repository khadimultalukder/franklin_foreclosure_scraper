# Franklin County Foreclosure Scraper

Scrapes the Franklin County, Ohio Clerk of Courts "Case Information Online"
system (`https://fcdcfcjs.co.franklin.oh.us/CaseInformationOnline/`) for
civil (`CV`) case filings, keeps the ones where `TYPE of CASE` is
`FORECLOSURES`, and appends them to a Google Sheet.

## Files

- `franklin_scraper.py` — the main script. Starting from a case
  number, walks forward one `caseSeq` at a time, keeps every `FORECLOSURES`
  case it finds, and appends those to the Google Sheet. This is the one you
  run on a schedule.
- `config/service_account.json` — Google service account credentials
  (secret, gitignored, **not on GitHub** — see below).
- `config/state.jsonl` — run history / resume point (also gitignored, not
  on GitHub, regenerated automatically — see "How resuming works" below).
- `requirements.txt` — Python dependencies.

> **`config/` is intentionally not in the git repo.** It's listed in
> `.gitignore` because it holds credentials and machine-local run state.
> That means if you `git clone` this repo fresh onto a new machine (or a
> cloud runner), the `config/` folder simply won't be there — you have to
> create it yourself (step 2 below). `state.jsonl` doesn't need to be
> created manually; the script creates it automatically on first run.

## Setup

1. Install dependencies:

   ```
   pip install -r requirements.txt
   ```

2. Google Sheets access: create a `config/` folder next to the scripts (if
   it doesn't already exist on this machine) and place the service account
   key at `config/service_account.json`. This file is never pushed to
   GitHub, so it has to be copied over manually to every machine that runs
   this script (your PC, a teammate's PC, a cloud runner, etc.) — get it
   from wherever the original key is stored/shared, not from git. The
   service account's email (the `client_email` field inside
   `service_account.json`) needs to be shared as **Editor** on the target
   Google Sheet. If you ever regenerate or swap the key, just drop the new
   JSON in at the same path.

3. Point the script at the target Google Sheet: open `franklin_scraper.py`
   and set `SHEET_ID` and `SHEET_TAB` near the top of the file:

   ```python
   SHEET_ID = "<the client's Google Sheet ID goes here>"
   SHEET_TAB = "Sheet1"
   ```

   The Sheet ID is the long string in the Sheet's URL:
   `https://docs.google.com/spreadsheets/d/<SHEET_ID>/edit`. This is the
   client's own Sheet — get the ID from them (and make sure the service
   account email above is shared as Editor on it, per step 2).

## Running it

```
python franklin_scraper.py
```

The starting case number lives at the bottom of the file:

```python
if __name__ == "__main__":
    caseYear = "26"
    caseType = "CV"
    startSeq = 5510   # only used on the very first run
```

- `caseYear` — 2-digit year, e.g. `"26"`.
- `caseType` — case type code, e.g. `"CV"` for civil.
- `startSeq` — the case sequence number to start the very first run from
  (e.g. `5510` for case `26 CV 005510`, about a month back from the current
  ceiling is reasonable for the initial backfill). **This is only used the
  first time you ever run the script for this caseYear/caseType** — every
  run after that resumes automatically (see below).

It walks forward one `caseSeq` at a time (`005510`, `005511`, `005512`,
...). Case numbers can have gaps (sealed/missing filings), so it doesn't
stop at the first miss — it only stops once it hits 10 consecutive misses
in a row (`MAX_CONSECUTIVE_MISSES` near the top of the file), which means
it's reached the current end of filed cases.

## How resuming works (high-water mark)

Every case checked gets appended as one line to `config/state.jsonl` —
this is a full audit trail (most checked cases aren't foreclosures, so the
Sheet alone wouldn't show that they were ever checked). Each line looks
like:

```json
{"timestamp": "...", "case_year": "26", "case_type": "CV", "case_seq": "005681", "result": "foreclosure", "last_seq": 5681, "checked": 172, "found": 27, "stop_reason": "checkpoint"}
```

On startup, the script reads the **last** line in `state.jsonl` for the
matching `case_year`/`case_type` and resumes from `last_seq + 1`,
completely ignoring `startSeq` in that case. So:

- **First run ever** (no `state.jsonl` yet, or no line for this
  year/type): starts from `startSeq`.
- **Every run after that**: picks up exactly where the last run left off.

**To force a re-scan from a specific case number** (e.g. you want to
re-check a range, or start over): delete `config/state.jsonl`, or edit it
by hand and remove/adjust the last matching line, then set `startSeq` to
wherever you want to resume from.

If a request fails (timeout/connection error after retries) or the script
crashes/is interrupted (including Ctrl+C), the state that gets saved
reflects the **last successfully completed case**, not further — so
nothing gets silently skipped. That case just gets retried on the next
run.

## Duplicate protection

On every run, the script reads the whole "Case Number" column already in
the Sheet before writing anything, and skips any foreclosure it finds that
already has a row there. This means it's always safe to re-run the script,
even over a range it's already covered — it will never create duplicate
rows, regardless of when a previous run happened to stop.

## Output columns

The Sheet gets rows appended (never overwritten) in this order:

| Case Number | Type of Case | Status | Date Filed | Defendant Name | Plaintiff Name | Case ID/Link |
|---|---|---|---|---|---|---|

**Case ID/Link** is just the case number again. The court's site has no
stable, bookmarkable URL for an individual case — every search generates a
one-time session token in the URL that isn't reusable later — so there's
nothing else to put there.

## Known limitations / out of scope

- **Property address is not included.** It's not on the case-detail page —
  it's inside the complaint PDF, which isn't parsed by this scraper (by
  design, per the original scope).
- **No cloud scheduling set up yet.** Right now this runs wherever you run
  it manually (e.g. your PC via `python franklin_scraper.py`). To run
  it unattended on a daily schedule (GitHub Actions cron, cloud function,
  etc.) is a separate deployment step not yet configured. Whichever option
  is used, remember `config/service_account.json` isn't in the repo (see
  above) — it'll need to be provided to the runner some other way, e.g. as
  a GitHub Actions secret that gets written to `config/service_account.json`
  at the start of the workflow, not committed to the repo.
- **Rate limiting:** the script waits 1 second between requests and retries
  failed requests up to 3 times with backoff, to be polite to an old
  government server. Don't lower `REQUEST_DELAY_SECONDS` aggressively.

## Troubleshooting

- **"NO CASE MATCHED THE SEARCH CRITERIA"** in the console just means that
  particular case number doesn't exist (sealed, or not filed) — this is
  normal and expected, not an error.
- **Script exits with a disclaimer error** — the "Conditions of Use" accept
  step didn't work; the site may have changed its disclaimer page.
- **Sheet writes fail** — double check the service account email has
  Editor access on the Sheet, and that `config/service_account.json` is the
  current, valid key.
