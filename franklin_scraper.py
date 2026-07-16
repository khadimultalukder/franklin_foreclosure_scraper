import re
import sys
import requests

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

payload = {
    'attyIdx': '', 'advFlag': '', 'reallySubmit': 'true',
    'lname': '', 'fname': '', 'mint': '', 'selType': ' ',
    'caseYear': '26', 'caseYear_h': '26',
    'caseType': 'CV', 'caseType_h': 'CV',
    'caseSeq': '005500', 'caseSeq_h': '005500',
    'personType': 'P', 'attyNum': '',
    'txtCalendar1': '', 'txtCalendar2': '', 'recs': '25',
}

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

