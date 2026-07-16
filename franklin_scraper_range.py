"""
Range walker for the Franklin County Case Information Online system.
Separate from franklin_scraper.py (that script is untouched).

Walks a range of caseSeq numbers (pagination by simple sequential increment,
e.g. 005510, 005511, 005512, ...) under one caseYear/caseType, and appends
any FORECLOSURES cases found to a CSV.

Handles:
  - one session/cookie jar reused across the whole range (only 1 disclaimer
    accept + only 1 "GET home" needed, not per-case)
  - missing/sealed case numbers -> detected via "NO CASE MATCHED THE SEARCH
    CRITERIA." and skipped
  - polite rate limiting between requests
  - retry with backoff on timeout / connection errors
"""
import csv
import os
import re
import sys
import time
import requests
from bs4 import BeautifulSoup

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

CSV_PATH = "results.csv"
CSV_FIELDS = ["case_number", "type", "status", "date_filed", "plaintiff_name", "defendant_name"]

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
    if type_of_case != "FORECLOSURES":
        return None

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


def save_to_csv(row: dict, path: str = CSV_PATH):
    file_exists = os.path.isfile(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)
    print(f"  -> saved to {path}")


def walk_range(case_year: str, case_type: str, start_seq: int, end_seq: int):
    """Pagination by simple caseSeq increment: 005510, 005511, 005512, ...
    One session reused for the whole range."""
    session = start_session()

    found = 0
    missing = 0
    checked = 0

    for seq in range(start_seq, end_seq + 1):
        case_seq = str(seq).zfill(6)
        checked += 1
        print(f"[{checked}] checking {case_year} {case_type} {case_seq} ...")

        html = fetch_case(session, case_year, case_type, case_seq)
        if html is None:
            # request itself failed after retries -- skip this case, keep going
            continue

        result = parse_case(html)
        if result == "missing":
            missing += 1
            print("  -> no case / sealed, skipping")
        elif result is None:
            print("  -> not a foreclosure")
        else:
            found += 1
            print(f"  -> FORECLOSURE: {result['case_number']} - {result['plaintiff_name']} v {result['defendant_name']}")
            save_to_csv(result)

        time.sleep(REQUEST_DELAY_SECONDS)

    print(f"\nDone. Checked {checked} cases, {missing} missing/sealed, {found} foreclosures found.")
    return found


if __name__ == "__main__":
    # only these need to change per run
    caseYear = "26"
    caseType = "CV"
    startSeq = 5510   # e.g. 005510
    endSeq = 5600     # e.g. 005520 (inclusive)

    walk_range(caseYear, caseType, startSeq, endSeq)
