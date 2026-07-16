"""
Franklin County Case Information Online scraper.

Starting from a single caseSeq you provide, walks forward one case at a time
(005510, 005511, 005512, ...) under one caseYear/caseType, and appends any
FORECLOSURES cases found to a Google Sheet (append-only, never overwrites
existing rows). Stops once MAX_CONSECUTIVE_MISSES case numbers in a row come
back empty -- that's treated as "reached the end."

Handles:
  - one session/cookie jar reused across the whole run (only 1 disclaimer
    accept + only 1 "GET home" needed, not per-case)
  - polite rate limiting between requests
  - retry with backoff on timeout / connection errors

Requires: pip install gspread google-auth
Uses the service account key at config/service_account.json -- make sure
that service account's email is shared as an Editor on the target Sheet.
"""
import re
import sys
import time
import requests
from bs4 import BeautifulSoup
import gspread
from google.oauth2.service_account import Credentials

BASE = "https://fcdcfcjs.co.franklin.oh.us/CaseInformationOnline/"
SEARCH_URL = "https://fcdcfcjs.co.franklin.oh.us/CaseInformationOnline/caseSearch"

USER_AGENT = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36')

COMMON_HEADERS = {
    'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
    'accept-language': 'en-US,en;q=0.5',
    'sec-ch-ua': '"Not;A=Brand";v="8", "Chromium";v="150", "Brave";v="150"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"macOS"',
    'sec-gpc': '1',
    'user-agent': USER_AGENT,
}

SERVICE_ACCOUNT_FILE = "config/service_account.json"
SHEET_ID = "1-4vsPPHH9m-vzbKa-fZ3mP7CrDPnEqwVAlVkfXhvitQ"
SHEET_TAB = "All_Case"
# NEW: second tab that only ever receives rows whose type_of_case is
# literally "FORECLOSURES" -- Sheet1 keeps getting every case regardless
# of type, unchanged from before.
SHEET_TAB_FORECLOSURES = "FORECLOSURES"
SHEET_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# order matches the client's required column order
SHEET_HEADERS = [
    "Case Number", "Type of Case", "Status", "Date Filed",
    "Defendant Name", "Plaintiff Name", "Case ID/Link",
]

# be polite to an old government server
REQUEST_DELAY_SECONDS = 1.0
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 5

def build_payload(case_year: str, case_type: str, case_seq: str) -> dict:
    return {
        'attyIdx': '',
        'advFlag': '',
        'reallySubmit': 'true',
        'lname': '',
        'fname': '',
        'mint': '',
        'selType': ' ',
        'caseYear': case_year,
        'caseYear_h': case_year,
        'caseType': case_type,
        'caseType_h': case_type,
        'caseSeq': case_seq,
        'caseSeq_h': case_seq,
        'personType': 'P',
        'attyNum': '',
        'txtCalendar1': '',
        'txtCalendar2': '',
        'recs': '25',
    }


def start_session() -> requests.Session:
    """One session for the whole range walk: GET home, accept disclaimer if shown."""
    session = requests.Session()
    session.headers.update(COMMON_HEADERS)

    home_resp = session.get(BASE, timeout=30)
    print("GET home ->", home_resp.status_code, len(home_resp.text), "bytes")

    if "Conditions of Use" in home_resp.text:
        m = re.search(r"acceptDisclaimer\?([^\"'>]+)", home_resp.text)
        if not m:
            sys.exit("Disclaimer page shown but couldn't find the acceptDisclaimer token")
        accept_url = f"{BASE}acceptDisclaimer?{m.group(1)}"
        accept_headers = {
            **COMMON_HEADERS,
            'content-type': 'application/x-www-form-urlencoded',
            'origin': 'https://fcdcfcjs.co.franklin.oh.us',
            'referer': BASE,
        }
        accept_resp = session.post(
            accept_url, headers=accept_headers,
            data={"fromPage": "index", "Accept": "ACCEPT"},
            timeout=30,
        )
        print("POST acceptDisclaimer ->", accept_resp.status_code, len(accept_resp.text), "bytes")
        if "Conditions of Use" in accept_resp.text:
            sys.exit("Still on the disclaimer page after accepting -- accept step did not stick")
    else:
        print("No disclaimer shown (session already had a prior 'accepted' cookie).")

    return session


def fetch_case(session: requests.Session, case_year: str, case_type: str, case_seq: str):
    """POST one case lookup, with retry on timeout/connection errors. Returns
    the response text, or None if it kept failing after MAX_RETRIES."""
    payload = build_payload(case_year, case_type, case_seq)
    search_headers = {
        **COMMON_HEADERS,
        'content-type': 'application/x-www-form-urlencoded',
        'origin': 'https://fcdcfcjs.co.franklin.oh.us',
        'referer': BASE,
    }

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = session.post(SEARCH_URL, headers=search_headers, data=payload, timeout=30)
            return response.text
        except (requests.Timeout, requests.ConnectionError) as e:
            print(f"  [{case_year} {case_type} {case_seq}] request failed (attempt {attempt}/{MAX_RETRIES}): {e}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
    print(f"  [{case_year} {case_type} {case_seq}] giving up after {MAX_RETRIES} attempts")
    return None


def parse_case(html: str):
    """Returns a result dict if this is a FORECLOSURES case, 'missing' if the
    case number doesn't exist / is sealed, or None if it exists but isn't a
    foreclosure (or the page didn't parse as expected)."""
    if "NO CASE MATCHED THE SEARCH CRITERIA" in html.upper():
        return "missing"

    soup = BeautifulSoup(html, "html.parser")
    case_summary = soup.select_one("section#case-summary-container tbody tr")
    if case_summary is None:
        return "missing"

    tds = case_summary.select("td")
    if len(tds) < 5:
        return "missing"

    type_of_case = tds[2].text.strip()
    # if type_of_case != "FORECLOSURES":
    #     return None

    case_number = tds[1].text.strip()
    status = tds[3].text.strip()
    date_filed = tds[4].text.strip()

    plaintiff_element = soup.select_one("tbody#plaintiff-body tr")
    plaintiff_name = plaintiff_element.select("td")[1].text.strip() if plaintiff_element else ""

    defendant_element = soup.select_one("tbody#defendant-body tr")
    defendant_name = defendant_element.select("td")[1].text.strip() if defendant_element else ""

    return {
        "case_number": case_number,
        "type": type_of_case,
        "status": status,
        "date_filed": date_filed,
        "plaintiff_name": plaintiff_name,
        "defendant_name": defendant_name,
    }


def get_worksheet():
    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SHEET_SCOPES)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SHEET_ID)
    ws = sh.worksheet(SHEET_TAB)
    if not ws.row_values(1):
        ws.append_row(SHEET_HEADERS, value_input_option="RAW")
    return ws


def get_foreclosures_worksheet():
    """NEW: same spreadsheet, second tab ('FORECLOSURES'). Created
    automatically if it doesn't exist yet. Only rows whose type_of_case ==
    'FORECLOSURES' get mirrored here -- everything else still only goes to
    Sheet1."""
    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SHEET_SCOPES)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SHEET_ID)
    try:
        ws = sh.worksheet(SHEET_TAB_FORECLOSURES)
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=SHEET_TAB_FORECLOSURES, rows=1000, cols=len(SHEET_HEADERS))
    if not ws.row_values(1):
        ws.append_row(SHEET_HEADERS, value_input_option="RAW")
    return ws


def get_existing_case_numbers(ws) -> set:
    """Read the whole 'Case Number' column (col A) so we can skip re-adding
    a case that's already in the sheet, and so the resume high-water mark
    can be derived from it too. The sheet itself is the single source of
    truth for "did I already write this case" and "where did I leave off."""
    col_a = ws.col_values(1)  # includes the header row
    return set(col_a[1:]) if col_a else set()


def save_to_sheet(ws, row: dict, existing_case_numbers: set):
    """gspread's append_row() calls the Sheets API values.append endpoint,
    which always inserts after the last row with data -- it cannot overwrite
    existing rows. Skips writing if this case_number is already present."""
    if row["case_number"] in existing_case_numbers:
        print(f"  -> {row['case_number']} already in the sheet, skipping (dup guard)")
        return

    values = [
        row["case_number"],
        row["type"],
        row["status"],
        row["date_filed"],
        row["defendant_name"],
        row["plaintiff_name"],
        row["case_number"],  # Case ID/Link -- site has no stable permalink, case number doubles as the ID/link
    ]
    ws.append_row(values, value_input_option="RAW")
    existing_case_numbers.add(row["case_number"])
    print("  -> appended to Google Sheet")


MAX_CONSECUTIVE_MISSES = 10


def load_high_water_mark_from_sheet(existing_case_numbers: set, case_year: str, case_type: str):
    """Resume source: the Google Sheet's own 'Case Number' column (no local
    state file). Scans existing_case_numbers (already read from the sheet)
    for entries belonging to this case_year/case_type,
    and returns the highest caseSeq found + 1 -- i.e. skip every case
    number already in the sheet and start right after the last one.
    Returns None if no matching case number is in the sheet yet (first run
    for this year/type combo, so startSeq is used instead)."""
    prefix = f"{case_year}{case_type}".upper()
    max_seq = None
    for case_number in existing_case_numbers:
        normalized = re.sub(r"\s+", "", case_number).upper()
        if not normalized.startswith(prefix):
            continue
        m = re.search(r"(\d+)$", normalized)
        if not m:
            continue
        seq = int(m.group(1))
        if max_seq is None or seq > max_seq:
            max_seq = seq
    return None if max_seq is None else max_seq + 1


def walk_from(case_year: str, case_type: str, start_seq: int):
    """Pagination by simple caseSeq increment: 005510, 005511, 005512, ...
    One session reused for the whole run. Individual case numbers can be
    gaps (sealed/missing) even while later numbers still have data, so this
    only stops once MAX_CONSECUTIVE_MISSES in a row come back empty -- any
    hit in between resets the miss counter.

    Resumes from the Google Sheet's own Case Number column (highest caseSeq
    already there, for this case_year/case_type) if a match exists --
    startSeq is only used on the very first run / first time this
    case_year/case_type shows up in the sheet."""
    session = start_session()
    ws = get_worksheet()
    existing_case_numbers = get_existing_case_numbers(ws)
    print(f"Writing to Google Sheet '{ws.spreadsheet.title}' / tab '{ws.title}' "
          f"({len(existing_case_numbers)} case(s) already in it)")

    # NEW: second tab that only receives type_of_case == "FORECLOSURES" rows
    ws_forecl = get_foreclosures_worksheet()
    existing_case_numbers_forecl = get_existing_case_numbers(ws_forecl)
    print(f"Also mirroring FORECLOSURES-only rows to tab '{ws_forecl.title}' "
          f"({len(existing_case_numbers_forecl)} case(s) already in it)")

    resume_seq = load_high_water_mark_from_sheet(existing_case_numbers, case_year, case_type)
    if resume_seq is not None:
        print(f"Resuming from Google Sheet high-water mark: {case_year} {case_type} "
              f"{str(resume_seq).zfill(6)} (ignoring startSeq={start_seq})")
        seq = resume_seq
    else:
        print(f"No matching case numbers found in the sheet yet -- starting fresh from "
              f"{case_year} {case_type} {str(start_seq).zfill(6)}")
        seq = start_seq

    found = 0
    checked = 0
    consecutive_misses = 0
    last_completed_seq = seq - 1  # nothing successfully checked yet

    while True:
        case_seq = str(seq).zfill(6)
        checked += 1
        print(f"[{checked}] checking {case_year} {case_type} {case_seq} ...")

        html = fetch_case(session, case_year, case_type, case_seq)
        if html is None:
            # request itself failed after retries -- stop here, and don't
            # advance the high-water mark past this case, so next run
            # retries it instead of silently skipping it
            print("  -> request kept failing, stopping.")
            break

        result = parse_case(html)
        reached_end_now = False

        if result == "missing":
            consecutive_misses += 1
            print(f"  -> no case_number came back ({consecutive_misses}/{MAX_CONSECUTIVE_MISSES} consecutive misses)")
            if consecutive_misses >= MAX_CONSECUTIVE_MISSES:
                print(f"  -> hit {MAX_CONSECUTIVE_MISSES} consecutive misses, stopping.")
                reached_end_now = True
        else:
            consecutive_misses = 0
            if result is None:
                print("  -> not a foreclosure")
            else:
                # only counts as found/complete once the sheet write itself
                # succeeds -- if this throws, last_completed_seq must NOT
                # advance past this case, so a retry re-attempts the write
                # instead of silently losing this foreclosure
                save_to_sheet(ws, result, existing_case_numbers)
                found += 1
                print(f"  -> FORECLOSURE: {result['case_number']} - {result['plaintiff_name']} v {result['defendant_name']}")

                # NEW: additionally mirror into the FORECLOSURES-only tab,
                # but only when the case's actual type_of_case is
                # "FORECLOSURES" -- Sheet1 above still gets every case
                # regardless of type
                if result["type"] == "FORECLOSURES":
                    save_to_sheet(ws_forecl, result, existing_case_numbers_forecl)

        # this case is now fully done (checked, and written if it was a
        # foreclosure) -- safe to advance the high-water mark
        last_completed_seq = seq

        if reached_end_now:
            break

        seq += 1
        time.sleep(REQUEST_DELAY_SECONDS)

    print(f"\nDone. Checked {checked} cases, {found} foreclosures found. "
          f"High-water mark now at {case_year} {case_type} {str(last_completed_seq).zfill(6)}.")
    return found


if __name__ == "__main__":
    caseYear = "26"
    caseType = "CV"
    startSeq = 5510   # e.g. 005510 -- only used on the very first run;
                      # after that, the Google Sheet's own high-water mark wins

    walk_from(caseYear, caseType, startSeq)
