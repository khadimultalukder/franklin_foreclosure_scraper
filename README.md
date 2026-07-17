# Franklin County Foreclosure Scraper

Scrapes the Franklin County, Ohio Clerk of Courts "Case Information Online"
system (`https://fcdcfcjs.co.franklin.oh.us/CaseInformationOnline/`) for
civil (`CV`) case filings, writes every case it finds to a Google Sheet
tab, and additionally mirrors the ones where `TYPE of CASE` is
`FORECLOSURES` into a second tab.

## Files

- `franklin_scraper.py` — the main script. Starting from a case
  number, walks forward one `caseSeq` at a time, writes every case it finds
  to the Google Sheet, and mirrors `FORECLOSURES` cases into a second tab.
  This is the one you run on a schedule.
- `run_scraper.bat` — Windows wrapper around the script (checks Python is
  installed, `cd`s into the project folder, runs the script unbuffered,
  and appends output to `logs\run_log.txt`). This is what Windows Task
  Scheduler should point at — see "Running on a schedule" below.
- `config/service_account.json` — Google service account credentials
  (secret, gitignored, **not on GitHub** — see below).
- `logs/run_log.txt` — created automatically by `run_scraper.bat` the
  first time it runs; gitignored, not on GitHub.
- `requirements.txt` — Python dependencies.

> **`config/` is intentionally not in the git repo.** It's listed in
> `.gitignore` because it holds credentials. That means if you `git clone`
> this repo fresh onto a new machine (or a cloud runner), the `config/`
> folder simply won't be there — you have to create it yourself (step 2
> below).

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
   SHEET_TAB = "All_Case"
   ```

   The Sheet ID is the long string in the Sheet's URL:
   `https://docs.google.com/spreadsheets/d/<SHEET_ID>/edit`. This is the
   client's own Sheet — get the ID from them (and make sure the service
   account email above is shared as Editor on it, per step 2).

   `SHEET_TAB_FORECLOSURES` (set to `"FORECLOSURES"`) is the second tab —
   the script creates it automatically on first run if it doesn't already
   exist, so there's nothing to set up for it by hand.

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

## Log format

Every line goes through a `log_message()` helper that prefixes a
`YYYY-MM-DD HH:MM:SS` timestamp. Two kinds of lines show up while it's
walking cases:

```
2026-07-17 00:15:10 - ✅ [148] 26 CV 006569 | FORECLOSURES | ACTIVE | 06/26/2026
2026-07-17 00:15:12 - ❌ [149] 26 CV 006570 | NO Case data available
```

- **✅** — a case was found and written to `All_Case` (and to
  `FORECLOSURES` too, if its type matched). Fields are pipe-delimited:
  case number, type of case, status, date filed.
- **❌** — that `caseSeq` didn't exist (sealed or not filed). Counts toward
  the `MAX_CONSECUTIVE_MISSES` stop condition.

Startup info (session/disclaimer, which Sheet tabs it's writing to, the
resume point) and the final "Done. Checked N cases..." summary also go
through `log_message()`, so they're timestamped too.

## Running on a schedule (Windows Task Scheduler)

`run_scraper.bat` is the entry point for unattended runs:

1. Checks `python` is on `PATH` (fails fast with a message if not, instead
   of Task Scheduler silently doing nothing).
2. `cd`s into the project folder — **update this path in the `.bat` file
   if you move the project**, since Task Scheduler starts a task in
   `C:\Windows\System32` by default, not the script's own folder.
3. Creates `logs\` if it doesn't exist yet.
4. Runs `python -u franklin_scraper.py >> logs\run_log.txt 2>&1`. The `-u`
   flag disables Python's output buffering — without it, log lines sit in
   memory and don't reach the file until the process exits, which makes a
   long-running task look like it's silently doing nothing.

To schedule it:

- Open Task Scheduler → **Create Task** (not "Basic Task", for full
  control).
- **General**: check "Run whether user is logged on or not" so it still
  runs if the RDP session disconnects.
- **Triggers**: New → Daily, pick a time.
- **Actions**: New → Program/script → point at `run_scraper.bat`. Leave
  "Start in" blank — the `.bat` handles `cd` itself.
- **Settings**: don't set an aggressive "stop the task if it runs longer
  than" limit — a run walking through a large backlog can take a while.

After a run (or while one is in progress, since output is unbuffered now),
check `logs\run_log.txt` to see what happened. No console window popping
up is normal — Task Scheduler runs tasks in a hidden session.

## How resuming works (high-water mark)

There's no local state file — the Google Sheet itself is the resume point.
On startup, the script reads the whole "Case Number" column of the
`SHEET_TAB` tab (`All_Case`), filters it down to case numbers belonging to
the current `caseYear`/`caseType`, and resumes from the **highest**
`caseSeq` found there **+ 1**, completely ignoring `startSeq` in that case.
So:

- **First run ever** (no case numbers in the sheet yet for this
  year/type): starts from `startSeq`.
- **Every run after that**: picks up right after the last case number
  already sitting in the Sheet.

**To force a re-scan from a specific case number** (e.g. you want to
re-check a range, or start over): remove the relevant rows from the
`All_Case` tab (and `FORECLOSURES` tab, if applicable) in the Sheet itself,
then set `startSeq` to wherever you want to resume from.

If a request fails (timeout/connection error after retries) or the script
crashes/is interrupted (including Ctrl+C), whatever was already written to
the Sheet stays put and nothing gets silently skipped — the next run just
resumes from the highest case number that made it into the Sheet, and
re-checks anything after that.

## Duplicate protection

On every run, the script reads the whole "Case Number" column already in
each tab (`All_Case` and `FORECLOSURES`) before writing anything, and skips
any case it finds that already has a row there. This means it's always
safe to re-run the script, even over a range it's already covered — it
will never create duplicate rows, regardless of when a previous run
happened to stop. This same column read is also what drives resuming (see
above), so there's nothing extra to maintain.

## Output columns

Each tab gets rows appended (never overwritten) in this order:

| Case Number | Type of Case | Status | Date Filed | Defendant Name | Plaintiff Name | Case ID/Link |
|---|---|---|---|---|---|---|

- **`All_Case`** — every case the script successfully looks up, regardless
  of type.
- **`FORECLOSURES`** — the subset of those rows where `Type of Case` is
  literally `FORECLOSURES`. Created automatically the first time the
  script runs if it doesn't already exist.

**Case ID/Link** is just the case number again. The court's site has no
stable, bookmarkable URL for an individual case — every search generates a
one-time session token in the URL that isn't reusable later — so there's
nothing else to put there.

## Known limitations / out of scope

- **Property address is not included.** It's not on the case-detail page —
  it's inside the complaint PDF, which isn't parsed by this scraper (by
  design, per the original scope).
- **Scheduling is local (Windows Task Scheduler), not cloud-based.** See
  "Running on a schedule" above. It runs on whichever Windows machine
  `run_scraper.bat` and `config/service_account.json` are set up on (e.g.
  the client's RDP server) — there's no cloud runner (GitHub Actions cron,
  cloud function, etc.) involved. Moving to one later is possible, but
  remember `config/service_account.json` isn't in the repo (see above) —
  it'd need to be provided to the runner some other way, e.g. as a GitHub
  Actions secret written to that path at the start of the workflow, not
  committed to the repo.
- **Rate limiting:** the script waits 1 second between requests and retries
  failed requests up to 3 times with backoff, to be polite to an old
  government server. Don't lower `REQUEST_DELAY_SECONDS` aggressively.

## Troubleshooting

- **"NO CASE MATCHED THE SEARCH CRITERIA"** in the console just means that
  particular case number doesn't exist (sealed, or not filed) — this is
  normal and expected, not an error, and shows up as a ❌ line (see "Log
  format" above).
- **Script exits with a disclaimer error** — the "Conditions of Use" accept
  step didn't work; the site may have changed its disclaimer page.
- **Sheet writes fail** — double check the service account email has
  Editor access on the Sheet, and that `config/service_account.json` is the
  current, valid key.
- **`UnicodeEncodeError: 'charmap' codec can't encode character...`** — this
  happened on older versions of the script when output was redirected to a
  file (`>> logs\run_log.txt`) on Windows: without a real console, Python
  falls back to the system codepage (e.g. `cp1252`), which can't encode the
  ✅/❌ icons. Fixed by forcing UTF-8 on stdout/stderr near the top of
  `franklin_scraper.py`. If you ever see this again, that's the first place
  to check.
- **Task Scheduler shows the task "Running" for a long time with an empty
  or missing `logs\run_log.txt`** — most likely output buffering, not a
  hang: Python fully buffers stdout when it isn't attached to a real
  console, so log lines can sit in memory for a long time before actually
  reaching the file. `run_scraper.bat` runs the script with `python -u`
  (unbuffered) specifically to avoid this — make sure that flag is still
  there if you've edited the `.bat`. No console window appearing is normal
  and not related to this; Task Scheduler runs tasks in a hidden session.
- **Task appears to run but nothing happens / nothing gets written** —
  check that `run_scraper.bat`'s `cd /d "..."` line points at wherever the
  project actually lives on that machine. Task Scheduler starts a task in
  `C:\Windows\System32` by default (not the `.bat` file's own folder), so
  if that path is stale after moving the project, the script can't find
  itself or `config/service_account.json`.
