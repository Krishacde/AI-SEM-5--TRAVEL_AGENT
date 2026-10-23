import os
import re

import certifi
import requests
from dotenv import load_dotenv

load_dotenv()

# Fix for SSL certificate issues in some environments
os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()

# RailRadar API credentials
RAILRADAR_API_KEY = os.getenv("RAILRADAR_API_KEY")
RAILRADAR_BASE_URL = "https://api.railradar.in/v1/trains/between"

CITY_TO_STATION_CODE = {
    "delhi": "NDLS",
    "new delhi": "NDLS",
    "mumbai": "MMCT",  # Updated to MMCT for IRCTC/RapidAPI standard
    "bombay": "MMCT",
    "kolkata": "HWH",
    "calcutta": "HWH",
    "bengaluru": "SBC",
    "bangalore": "SBC",
    "chennai": "MAS",
    "madras": "MAS",
    "hyderabad": "HYB",
    "ahmedabad": "AMD",
    "pune": "PUNE",
    "jaipur": "JP",
    "lucknow": "LKO",
    "patna": "PNBE",
    "bhubaneswar": "BBS",
    "guwahati": "GHY",
    "kochi": "ERS",
    "cochin": "ERS",
    "thiruvananthapuram": "TVC",
    "trivandrum": "TVC",
    "goa": "MAO",
    "madgaon": "MAO",
    "kanpur": "CNB",
    "nagpur": "NGP",
    "visakhapatnam": "VSKP",  # Updated to VSKP
    "vizag": "VSKP",
    "bhopal": "BPL",
    "indore": "INDB",
    "mysore": "MYS",
    "mysuru": "MYS",
    "coimbatore": "CBE",
    "nashik": "NK",
    "surat": "ST",
    "vadodara": "BRC",  # Updated to BRC
    "howrah": "HWH",
    "mumbai central": "MMCT",
    "new delhi railway station": "NDLS",
    "delhi cantt": "DEC",
    "agra": "AGC",
    "varanasi": "BSB",
    "ranchi": "RNC",
    "jabalpur": "JBP",
    "allahabad": "PRYJ",
    "prayagraj": "PRYJ",
     "dwarka": "DWK",
    "okha": "OKHA",
     "udaipur": "UDZ",
}

# ---------------------------------------------------------------------------
# REGION / STATE / HILL-TOWN → nearest practical railhead(s)
# Only entries for places that have NO direct station or are commonly passed
# as broad region names by the agent. All codes verified against IRCTC data.
# ---------------------------------------------------------------------------
REGION_TO_NEAREST_RAILHEAD = {
    # Himachal Pradesh — state and main tourist destinations
    "himachal pradesh": [("KLK", "Kalka"), ("UHL", "Una Himachal"), ("CDG", "Chandigarh")],
    "himachal":         [("KLK", "Kalka"), ("UHL", "Una Himachal"), ("CDG", "Chandigarh")],
    "shimla":           [("SML", "Shimla"), ("KLK", "Kalka")],
    "manali":           [("KLK", "Kalka"), ("CDG", "Chandigarh")],
    "manali himachal":  [("KLK", "Kalka"), ("CDG", "Chandigarh")],
    "kullu":            [("KLK", "Kalka"), ("CDG", "Chandigarh")],
    "dharamshala":      [("PTKC", "Pathankot Cantt"), ("CDG", "Chandigarh")],
    "mcleod ganj":      [("PTKC", "Pathankot Cantt"), ("CDG", "Chandigarh")],
    "dalhousie":        [("PTKC", "Pathankot Cantt"), ("CDG", "Chandigarh")],
    "chamba":           [("PTKC", "Pathankot Cantt"), ("JAT", "Jammu Tawi")],
    "spiti":            [("KLK", "Kalka"), ("CDG", "Chandigarh")],
    "kasauli":          [("KLK", "Kalka"), ("CDG", "Chandigarh")],
    "joginder nagar":   [("JDNX", "Joginder Nagar"), ("KLK", "Kalka")],
    # Uttarakhand — state and main destinations
    "uttarakhand":      [("HW", "Haridwar"), ("DDN", "Dehradun")],
    "haridwar":         [("HW", "Haridwar")],
    "rishikesh":        [("HW", "Haridwar"), ("DDN", "Dehradun")],
    "dehradun":         [("DDN", "Dehradun")],
    "mussoorie":        [("DDN", "Dehradun")],
    "nainital":         [("KTG", "Kathgodam"), ("MB", "Moradabad")],
    "jim corbett":      [("RMR", "Ramnagar"), ("MB", "Moradabad")],
    "corbett":          [("RMR", "Ramnagar"), ("MB", "Moradabad")],
    "auli":             [("HW", "Haridwar"), ("DDN", "Dehradun")],
    "kedarnath":        [("HW", "Haridwar"), ("DDN", "Dehradun")],
    "badrinath":        [("HW", "Haridwar"), ("DDN", "Dehradun")],
    # Jammu & Kashmir / Ladakh
    "jammu kashmir":     [("JAT", "Jammu Tawi")],
    "jammu and kashmir": [("JAT", "Jammu Tawi")],
    "srinagar":          [("JAT", "Jammu Tawi")],
    "gulmarg":           [("JAT", "Jammu Tawi")],
    "pahalgam":          [("JAT", "Jammu Tawi")],
    "sonamarg":          [("JAT", "Jammu Tawi")],
    "leh":               [("JAT", "Jammu Tawi")],
    "ladakh":            [("JAT", "Jammu Tawi")],
    # Rajasthan destinations without a direct main-line station
    "mount abu":         [("ABR", "Abu Road")],
    "ranthambore":       [("SWM", "Sawai Madhopur")],
    # Northeast
    "meghalaya":         [("GHY", "Guwahati")],
    "shillong":          [("GHY", "Guwahati")],
    "arunachal pradesh": [("NTSK", "Naharlagun"), ("GHY", "Guwahati")],
    "sikkim":            [("NJP", "New Jalpaiguri"), ("GHY", "Guwahati")],
    "gangtok":           [("NJP", "New Jalpaiguri")],
    "darjeeling":        [("NJP", "New Jalpaiguri")],
    
}


def normalize_location_text(value: str) -> str:
    text = (value or "").strip().lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_train_date(date_value: str) -> str:
    """Convert YYYY-MM-DD → DD-MM-YYYY (kept for display/legacy use; NOT used for RailRadar)."""
    if not date_value:
        return date_value

    cleaned = str(date_value).strip()

    try:
        from datetime import datetime
        return datetime.strptime(cleaned, "%Y-%m-%d").strftime("%d-%m-%Y")
    except ValueError:
        pass

    try:
        from datetime import datetime
        return datetime.strptime(cleaned, "%d-%m-%Y").strftime("%d-%m-%Y")
    except ValueError:
        return cleaned


def _minutes_to_duration(minutes) -> str:
    """Convert an integer number of minutes to a human-readable 'Xh Ym' string."""
    if not isinstance(minutes, int):
        return str(minutes)
    return f"{minutes // 60}h {minutes % 60}m"


# ---------------------------------------------------------------------------
# Resolution helpers
# ---------------------------------------------------------------------------

def _lookup_city_stations(clean: str) -> list:
    """Return [(code, display_name), …] from CITY_TO_STATION_CODE, or []."""
    if clean in CITY_TO_STATION_CODE:
        code = CITY_TO_STATION_CODE[clean]
        return [(code, clean.title())]

    for city, code in CITY_TO_STATION_CODE.items():
        if clean == city or clean in city or city in clean:
            return [(code, city.title())]

    return []


def _lookup_region_railheads(clean: str) -> list:
    """Return [(code, display_name), …] from REGION_TO_NEAREST_RAILHEAD, or []."""
    if clean in REGION_TO_NEAREST_RAILHEAD:
        return list(REGION_TO_NEAREST_RAILHEAD[clean])

    for region, railheads in REGION_TO_NEAREST_RAILHEAD.items():
        if clean == region or clean in region or region in clean:
            return list(railheads)

    return []


def _try_segments(raw_text: str) -> list:
    """
    Split 'Manali, Himachal Pradesh' on commas / 'and' / '&' into segments
    and try each independently — preferring a specific city match over a
    region railhead match. Returns the first non-empty result found.
    """
    segments = re.split(r"[,&]|\band\b", raw_text, flags=re.IGNORECASE)
    city_hits = []
    region_hits = []
    for seg in segments:
        clean_seg = normalize_location_text(seg)
        if not clean_seg:
            continue
        hit = _lookup_city_stations(clean_seg)
        if hit:
            city_hits.extend(hit)
        else:
            hit = _lookup_region_railheads(clean_seg)
            if hit:
                region_hits.extend(hit)
    # Prefer specific city results over region fallbacks; deduplicate preserving order
    combined = city_hits or region_hits
    seen = set()
    deduped = []
    for code, name in combined:
        if code not in seen:
            seen.add(code)
            deduped.append((code, name))
    return deduped


def resolve_station_candidates(location: str) -> list:
    """
    Resolve a free-text location to a list of (station_code, display_name) tuples.

    Resolution order:
      1. Bare station code pass-through (e.g. "NDLS", "BRC", "KLK")
      2. Exact / fuzzy match in CITY_TO_STATION_CODE
      3. Exact / fuzzy match in REGION_TO_NEAREST_RAILHEAD
      4. Segment-split (e.g. "Manali, Himachal Pradesh") — repeat 2+3 per segment

    Returns [] if nothing matched.
    """
    if not location:
        return []

    raw = str(location).strip()

    # 1. Bare station code (2–5 ALL-UPPERCASE letters with no spaces).
    #    Lowercase inputs like "Delhi" or "Agra" must NOT match here — they
    #    must fall through to the city dict so "Delhi" → NDLS, not "DELHI".
    if re.fullmatch(r"[A-Z]{2,5}", raw):
        return [(raw, raw)]

    clean = normalize_location_text(raw)
    if not clean:
        return []

    # 2. City dict (whole string)
    hits = _lookup_city_stations(clean)
    if hits:
        return hits

    # 3a. If the raw input has explicit delimiters (comma / & / 'and'), try
    #     segment-level resolution BEFORE the broad whole-string region fuzzy
    #     match — so "Shimla, Himachal Pradesh" resolves to Shimla's railhead
    #     rather than the broader Himachal Pradesh set.
    if re.search(r"[,&]|\band\b", raw, flags=re.IGNORECASE):
        hits = _try_segments(raw)
        if hits:
            return hits

    # 3b. Region railhead dict (whole-string fuzzy)
    hits = _lookup_region_railheads(clean)
    if hits:
        return hits

    # 4. Segment splitting — final fallback for inputs without explicit delimiters
    hits = _try_segments(raw)
    if hits:
        return hits

    return []


# ---------------------------------------------------------------------------
# RailRadar API — fetch layer
# ---------------------------------------------------------------------------

def _fetch_between(
    origin_code: str,
    destination_code: str,
    date: str,
    by_city: bool = False,
    limit: int = 10,
) -> list:
    """
    Call RailRadar GET /v1/trains/between/{from}/{to} and return a list of
    result dicts in the canonical shape expected by search_trains() / format_train().

    Args:
        origin_code:      IRCTC station code (e.g. "NDLS")
        destination_code: IRCTC station code (e.g. "AGC")
        date:             YYYY-MM-DD — passed through as-is (RailRadar requires this format)
        by_city:          If True, RailRadar expands the search to cover all stations
                          in the same metro area as each code (e.g. NDLS also checks
                          NZM, ANVT automatically). Use for ordinary single-station cities.
        limit:            Max number of results to return from this single call.

    Returns [] on any error or 404/NOT_FOUND — caller handles messaging.
    """
    if not RAILRADAR_API_KEY:
        return []

    url = f"{RAILRADAR_BASE_URL}/{origin_code}/{destination_code}"
    headers = {"Authorization": f"Bearer {RAILRADAR_API_KEY}"}
    params = {
        "date":   date,
        "byCity": "true" if by_city else "false",
    }

    try:
        response = requests.get(url, headers=headers, params=params, timeout=30)
        # Treat 404 as a clean "no trains" rather than a crash
        if response.status_code == 404:
            return []
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.RequestException:
        return []
    except ValueError:
        return []

    if not data.get("success"):
        return []

    trains_payload = data.get("data", {}).get("trains", [])
    from_info = data.get("data", {}).get("from", {})
    to_info   = data.get("data", {}).get("to", {})

    results = []
    for item in trains_payload[:limit]:
        train_info = item.get("train", {})
        from_sched = item.get("from", {})
        to_sched   = item.get("to", {})

        results.append({
            "train_name":             train_info.get("name", "Unknown Train"),
            "train_number":           train_info.get("number", "N/A"),
            "departure_station":      from_info.get("name", origin_code),
            "arrival_station":        to_info.get("name", destination_code),
            "departure_station_code": from_info.get("code", origin_code),
            "arrival_station_code":   to_info.get("code", destination_code),
            "departure_time":         from_sched.get("departure", "N/A"),
            "arrival_time":           to_sched.get("arrival", "N/A"),
            "duration":               _minutes_to_duration(item.get("duration")),
            "available_classes":      [],   # not provided by this endpoint
            "starting_fare":          None, # not provided by this endpoint
            "status":                 "Scheduled",
        })

    return results


def format_train(train: dict):
    train_name = train.get("train_name") or "Unknown train"
    train_number = train.get("train_number") or "N/A"
    departure_station = train.get("departure_station") or "Unknown"
    arrival_station = train.get("arrival_station") or "Unknown"
    departure_time = train.get("departure_time") or "N/A"
    arrival_time = train.get("arrival_time") or "N/A"
    duration = train.get("duration") or "N/A"
    classes = ", ".join(train.get("available_classes") or []) or "N/A"
    status = train.get("status") or "UNAVAILABLE"

    return f"""
Train: {train_name}
Number: {train_number}
Status: {status}

Route:
- From: {departure_station}
- To: {arrival_station}
- Departure: {departure_time}
- Arrival: {arrival_time}
- Duration: {duration}
- Classes: {classes}
""".strip()


def search_trains(origin: str, destination: str, date: str, limit: int = 5):
    if not RAILRADAR_API_KEY:
        return (
            "Train API error: RAILRADAR_API_KEY is missing.\n"
            "Please add it in your .env file:\n"
            "RAILRADAR_API_KEY=your_railradar_api_key_here"
        )

    origin_candidates = resolve_station_candidates(origin)
    destination_candidates = resolve_station_candidates(destination)

    # Report exactly which location(s) failed to resolve
    failed = []
    if not origin_candidates:
        failed.append(f"'{origin}'")
    if not destination_candidates:
        failed.append(f"'{destination}'")
    if failed:
        locations = " and ".join(failed)
        return (
            f"No railway station or nearby railhead found for {locations}. "
            "Try a nearby city with rail access (e.g. the nearest major junction)."
        )

    # -----------------------------------------------------------------------
    # Step 3 — Geography-aware calling logic
    #
    # len == 1  → ordinary single-station city  → 1 call, byCity=True
    #             (RailRadar's metro-area expansion covers nearby stations)
    # len >= 2  → region railhead fallback       → loop candidates, byCity=False
    #             (Kalka / Una / Chandigarh are genuinely different cities;
    #              byCity on just one would not find the others)
    # -----------------------------------------------------------------------

    # Build the list of (origin_code, dest_code, by_city) call pairs to try,
    # capped at MAX_CALLS to protect the 1,000-req/month free tier.
    MAX_CALLS = 4
    call_pairs = []

    if len(origin_candidates) == 1 and len(destination_candidates) == 1:
        # Common case: both sides are ordinary cities → single call, byCity=True
        call_pairs.append((origin_candidates[0][0], destination_candidates[0][0], True))

    elif len(origin_candidates) == 1:
        # Origin is a single city; destination is a broad region
        o_code = origin_candidates[0][0]
        for d_code, _ in destination_candidates:
            if len(call_pairs) >= MAX_CALLS:
                break
            call_pairs.append((o_code, d_code, False))

    elif len(destination_candidates) == 1:
        # Destination is a single city; origin is a broad region
        d_code = destination_candidates[0][0]
        for o_code, _ in origin_candidates:
            if len(call_pairs) >= MAX_CALLS:
                break
            call_pairs.append((o_code, d_code, False))

    else:
        # Both sides are broad regions — try cross-product up to MAX_CALLS
        for o_code, _ in origin_candidates:
            for d_code, _ in destination_candidates:
                if len(call_pairs) >= MAX_CALLS:
                    break
                call_pairs.append((o_code, d_code, False))
            if len(call_pairs) >= MAX_CALLS:
                break

    # Execute call pairs; stop as soon as results are found (quota-conserving)
    all_results = []
    seen_numbers = set()
    calls_made = 0

    for o_code, d_code, by_city in call_pairs:
        batch = _fetch_between(o_code, d_code, date, by_city=by_city, limit=limit)
        calls_made += 1
        for train in batch:
            num = train.get("train_number")
            if num not in seen_numbers:
                seen_numbers.add(num)
                all_results.append(train)
        if all_results:
            # Results found — stop here to conserve monthly quota
            break

    if not all_results:
        return (
            f"No trains found between '{origin}' and '{destination}' after checking "
            f"{calls_made} station combination(s) on {date}. "
            "Metro-area expansion (for cities) and alternate regional railheads "
            "(for broad regions) were both already tried."
        )

    # Sort by duration (shortest first), trim to requested limit
    all_results.sort(key=lambda t: t.get("duration", ""))
    return all_results[:limit]


if __name__ == "__main__":
    print("Fetching trains...\n")

    train_results = search_trains("Delhi", "Agra", "2026-10-09", limit=5)

    if isinstance(train_results, str):
        print(train_results)
    else:
        for train in train_results:
            print(format_train(train))
            print("-" * 20)