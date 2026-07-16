import csv
import os
import re
import sys
import requests
from bs4 import BeautifulSoup

CSV_PATH = "results.csv"
CSV_FIELDS = ["type", "case_number", "status", "date_filed", "plaintiff_name", "defendant_name"]

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


def build_payload(case_year: str, case_type: str, case_seq: str) -> dict:
    """Only caseYear/caseType/caseSeq change per case. Everything else below
    is fixed for a case-number search and doesn't need to be an input."""
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


def save_to_csv(row: dict, path: str = CSV_PATH):
    file_exists = os.path.isfile(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)
    print(f"Saved to {path}")


def search_case(case_year: str, case_type: str, case_seq: str):
    payload = build_payload(case_year, case_type, case_seq)

    session = requests.Session()
    session.headers.update(COMMON_HEADERS)

    # --- Step 1: GET the homepage to get a fresh session (JSESSIONID / _cfuvid) ---
    home_resp = session.get(BASE, timeout=30)
    print("GET home ->", home_resp.status_code, len(home_resp.text), "bytes")

    # --- Step 2: a brand-new session (no cookies yet) isn't shown the search
    # form -- it's shown a one-time "Conditions of Use and Privacy Policy"
    # interstitial with an ACCEPT button instead. That page's form posts to:
    #   /CaseInformationOnline/acceptDisclaimer?<random-token>
    # with fromPage=index&Accept=ACCEPT. The token is regenerated on every
    # request so it must be scraped fresh each run -- it can't be hardcoded.
    current = home_resp
    if "Conditions of Use" in current.text:
        m = re.search(r"acceptDisclaimer\?([^\"'>]+)", current.text)
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
        current = accept_resp
        # after accepting, the site should now be showing the real search
        # homepage -- if not, something about the accept step didn't stick
        if "Conditions of Use" in current.text:
            sys.exit("Still on the disclaimer page after accepting -- accept step did not stick")
    else:
        print("No disclaimer shown (session already had a prior 'accepted' cookie).")

    # --- Step 3: submit the case search, referer = homepage (this is a first
    # search from the homepage, not a "next case" click, so referer should be
    # the homepage URL, not the caseSearch URL) ---
    search_headers = {
        **COMMON_HEADERS,
        'content-type': 'application/x-www-form-urlencoded',
        'origin': 'https://fcdcfcjs.co.franklin.oh.us',
        'referer': BASE,
    }
    response = session.post(SEARCH_URL, headers=search_headers, data=payload, timeout=30)
    print("POST caseSearch ->", response.status_code, len(response.text), "bytes")

    if "NullPointerException" in response.text:
        print("WARNING: site returned an error page (NullPointerException) - see case_search.html")
    elif "CASE DETAIL" not in response.text.upper() and "CASE LISTING" not in response.text.upper():
        print("WARNING: response doesn't look like a case result page - see case_search.html")

    soup = BeautifulSoup(response.text, "html.parser")
    case_summary = soup.select_one("section#case-summary-container tbody tr")
    if case_summary is None:
        print("No case summary row found - see case_search.html")
        return None

    case_summary_td = case_summary.select("td")
    type_of_case = case_summary_td[2].text
    if type_of_case == "FORECLOSURES":
        print("Detected FORECLOSURES case")
        case_number = case_summary_td[1].text.strip()
        status = case_summary_td[3].text.strip()
        date_filed = case_summary_td[4].text.strip()

        plaintiff_element = soup.select_one("tbody#plaintiff-body tr")
        plaintiff_name = plaintiff_element.select("td")[1].text.strip()

        defendant_element = soup.select_one("tbody#defendant-body tr")
        defendant_name = defendant_element.select("td")[1].text.strip()


        print(f"{type_of_case} - {case_number} - {status} - {date_filed} - {plaintiff_name} - {defendant_name}")
        result = {
            "type": type_of_case,
            "case_number": case_number,
            "status": status,
            "date_filed": date_filed,
            "plaintiff_name": plaintiff_name,
            "defendant_name": defendant_name,
        }
        save_to_csv(result)
        return result
    else:
        print("No FORECLOSURES case")
        return None


if __name__ == "__main__":
    # only these 3 need to change per case
    caseYear = "26"
    caseType = "CV"
    caseSeq = "005510"

    search_case(caseYear, caseType, caseSeq)
