import json
import logging
import math
import os
import re
import threading
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path

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
GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"

logger = logging.getLogger(__name__)

_FETCH_CACHE_LOCK = threading.Lock()
_FETCH_CACHE: dict[tuple[str, str, str, bool], Future] = {}
_GEOCODE_CACHE: dict[str, tuple[float, float] | None] = {}
_GEOCODE_CACHE_LOCK = threading.Lock()
_RAILHEAD_CACHE: dict[str, list[tuple[str, str]]] = {}
_RAILHEAD_DISTANCE_KM: dict[str, tuple[str, str, float]] = {}
_RAILHEAD_CACHE_LOCK = threading.Lock()

# Approximate base fares from RailRadar's fare guide, accessed 2026-10-25:
# https://railradar.in/fare. These exclude reservation fees and surcharges.
RAIL_BASE_FARE_RATES_INR_PER_KM = {
    "1A": 3.0,
    "2A": 1.8,
    "3A": 1.3,
    "3E": 1.2,
    "CC": 1.0,
    "SL": 0.6,
    "2S": 0.4,
}

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
     "Mysuru":"MYS",
}

# ---------------------------------------------------------------------------
# REGION / STATE / HILL-TOWN → nearest practical railhead(s)
# Only entries for places that have NO direct station or are commonly passed
# as broad region names by the agent. All codes verified against IRCTC data.
# ---------------------------------------------------------------------------
REGION_TO_NEAREST_RAILHEAD = {
    "coorg":           [("MYS", "Mysuru")],
    "kodagu":          [("MYS", "Mysuru")],
    "madikeri":        [("MYS", "Mysuru")],
    "kushalnagar":     [("MYS", "Mysuru")],
    "virajpet":        [("MYS", "Mysuru")],
    # Himachal Pradesh
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
    
    # Uttarakhand
    "uttarakhand":      [("HW", "Haridwar"), ("DDN", "Dehradun")],
    "haridwar":         [("HW", "Haridwar")],
    "rishikesh":        [("HW", "Haridwar"), ("DDN", "Dehradun")],
    "dehradun":         [("DDN", "Dehradun")],
    "mussoorie":        [("DDN", "Dehradun")],
    "nainital":         [("KGM", "Kathgodam"), ("MB", "Moradabad")],
    "jim corbett":      [("RMR", "Ramnagar"), ("MB", "Moradabad")],
    "corbett":          [("RMR", "Ramnagar"), ("MB", "Moradabad")],
    "auli":             [("HW", "Haridwar"), ("DDN", "Dehradun")],
    "kedarnath":        [("HW", "Haridwar"), ("DDN", "Dehradun")],
    "badrinath":        [("HW", "Haridwar"), ("DDN", "Dehradun")],
    "valley of flowers":[("HW", "Haridwar"), ("DDN", "Dehradun")],
    "lansdowne":        [("KTW", "Kotdwar")],
    "kainchi dham":     [("KGM", "Kathgodam"), ("HDW", "Haldwani")],
    
    # Jammu, Kashmir & Ladakh
    "jammu kashmir":     [("JAT", "Jammu Tawi"), ("SVDK", "SMVD Katra")],
    "jammu and kashmir": [("JAT", "Jammu Tawi"), ("SVDK", "SMVD Katra")],
    "srinagar":          [("SINA", "Srinagar"), ("JAT", "Jammu Tawi")], # Udhampur-Srinagar line is getting active
    "gulmarg":           [("JAT", "Jammu Tawi")],
    "pahalgam":          [("JAT", "Jammu Tawi")],
    "sonamarg":          [("JAT", "Jammu Tawi")],
    "leh":               [("JAT", "Jammu Tawi"), ("CDG", "Chandigarh")],
    "ladakh":            [("JAT", "Jammu Tawi"), ("CDG", "Chandigarh")],
    "katra":             [("SVDK", "SMVD Katra")],
    "vaishno devi":      [("SVDK", "SMVD Katra")],

    # Rajasthan
    "rajasthan":         [("JP", "Jaipur"), ("UDZ", "Udaipur"), ("JU", "Jodhpur")],
    "mount abu":         [("ABR", "Abu Road")],
    "ranthambore":       [("SWM", "Sawai Madhopur")],
    "pushkar":           [("AII", "Ajmer Jn")],
    "jaisalmer":         [("JSM", "Jaisalmer"), ("JU", "Jodhpur")],
    "bikaner":           [("BKN", "Bikaner")],
    "udaipur":           [("UDZ", "Udaipur")],
    
    # Gujarat
    "kutch":             [("BHUJ", "Bhuj"), ("GIMB", "Gandhidham")],
    "rann of kutch":     [("BHUJ", "Bhuj"), ("GIMB", "Gandhidham")],
    "gir":               [("JND", "Junagadh"), ("VRL", "Veraval")],
    "statue of unity":   [("EKNR", "Ekta Nagar"), ("BRC", "Vadodara")],
    "somnath":           [("SMNH", "Somnath"), ("VRL", "Veraval")],
    "dwarka":            [("DWK", "Dwarka")],
    
    # Maharashtra
    "mahabaleshwar":     [("WTR", "Wathar"), ("PUNE", "Pune")],
    "panchgani":         [("WTR", "Wathar"), ("PUNE", "Pune")],
    "lonavala":          [("LNL", "Lonavala"), ("PUNE", "Pune")],
    "khandala":          [("LNL", "Lonavala")],
    "matheran":          [("NRL", "Neral")],
    "ajanta ellora":     [("AWB", "Aurangabad"), ("JL", "Jalgaon")],
    "aurangabad":        [("AWB", "Aurangabad")],
    "shirdi":            [("SNSI", "Sainagar Shirdi"), ("MMR", "Manmad")],
    "tadoba":            [("CD", "Chandrapur"), ("NGP", "Nagpur")],
    
    # Goa
    "goa":               [("MAO", "Madgaon"), ("THVM", "Thivim")],
    "north goa":         [("THVM", "Thivim"), ("KRMI", "Karmali")],
    "south goa":         [("MAO", "Madgaon"), ("VSG", "Vasco Da Gama")],
    "dudhsagar":         [("QLM", "Kulem"), ("MAO", "Madgaon")],
    
    # Karnataka
    "coorg":             [("MYS", "Mysuru")],
    "madikeri":          [("MYS", "Mysuru")],
    "hampi":             [("HPT", "Hosapete")],
    "gokarna":           [("GOK", "Gokarna Road"), ("KT", "Kumta")],
    "chikmagalur":       [("CMGR", "Chikkamagaluru"), ("HAS", "Hassan")],
    "bandipur":          [("MYS", "Mysuru")],
    "kabini":            [("MYS", "Mysuru")],
    "dandeli":           [("LWR", "Alnavar"), ("UBL", "Hubballi")],
    "udupi":             [("UD", "Udupi")],
    
    # Kerala
    "kerala":            [("ERN", "Ernakulam"), ("TVC", "Trivandrum")],
    "munnar":            [("AWY", "Aluva"), ("ERN", "Ernakulam")],
    "wayanad":           [("CLT", "Kozhikode")],
    "alleppey":          [("ALLP", "Alappuzha"), ("ERN", "Ernakulam")],
    "kumarakom":         [("KTYM", "Kottayam")],
    "thekkady":          [("KTYM", "Kottayam")],
    "varkala":           [("VAK", "Varkala Sivagiri"), ("TVC", "Trivandrum")],
    "kovalam":           [("TVC", "Trivandrum")],
    "kochi":             [("ERN", "Ernakulam")],
    
    # Tamil Nadu
    "ooty":              [("UAM", "Udagamandalam"), ("CBE", "Coimbatore")],
    "kodaikanal":        [("KQN", "Kodaikanal Road"), ("MDU", "Madurai")],
    "mahabalipuram":     [("CGL", "Chengalpattu"), ("MAS", "Chennai")],
    "pondicherry":       [("PDY", "Puducherry"), ("VM", "Villupuram")], # UT geographically in TN
    "rameswaram":        [("RMM", "Rameswaram"), ("MDU", "Madurai")],
    "kanyakumari":       [("CAPE", "Kanyakumari"), ("NCJ", "Nagercoil")],
    "mudumalai":         [("CBE", "Coimbatore"), ("MYS", "Mysuru")],
    "yercaud":           [("SA", "Salem")],
    
    # Madhya Pradesh & Chhattisgarh
    "khajuraho":         [("KURJ", "Khajuraho"), ("MCSC", "MCRS Chhatarpur")],
    "bandhavgarh":       [("UMR", "Umaria"), ("KTE", "Katni")],
    "kanha":             [("G", "Gondia"), ("JBP", "Jabalpur")],
    "pench":             [("NGP", "Nagpur")],
    "pachmarhi":         [("PPI", "Pipariya")],
    "ujjain":            [("UJN", "Ujjain")],
    "orchha":            [("VGLJ", "VGL Jhansi")],
    
    # Uttar Pradesh & Bihar
    "taj mahal":         [("AGC", "Agra Cantt"), ("AF", "Agra Fort")],
    "agra":              [("AGC", "Agra Cantt"), ("AF", "Agra Fort")],
    "varanasi":          [("BSB", "Varanasi"), ("DDU", "Pt. DD Upadhyaya")],
    "kashi":             [("BSB", "Varanasi")],
    "ayodhya":           [("AY", "Ayodhya Dham"), ("AYC", "Ayodhya Cantt")],
    "mathura":           [("MTJ", "Mathura")],
    "vrindavan":         [("MTJ", "Mathura")],
    "bodh gaya":         [("GAYA", "Gaya")],
    
    # West Bengal & Odisha
    "darjeeling":        [("NJP", "New Jalpaiguri")],
    "sundarbans":        [("CAN", "Canning"), ("SDAH", "Sealdah")],
    "digha":             [("DGHA", "Digha")],
    "puri":              [("PURI", "Puri"), ("BBS", "Bhubaneswar")],
    "konark":            [("PURI", "Puri"), ("BBS", "Bhubaneswar")],
    
    # Northeast (Meghalaya, Sikkim, Assam, Arunachal)
    "meghalaya":         [("GHY", "Guwahati")],
    "shillong":          [("GHY", "Guwahati")],
    "cherrapunji":       [("GHY", "Guwahati")],
    "arunachal pradesh": [("NTSK", "Naharlagun"), ("GHY", "Guwahati")],
    "tawang":            [("TZTB", "Tezpur"), ("GHY", "Guwahati")],
    "sikkim":            [("NJP", "New Jalpaiguri")],
    "gangtok":           [("NJP", "New Jalpaiguri")],
    "kaziranga":         [("FKG", "Furkating"), ("GHY", "Guwahati")],
    "majuli":            [("JTTN", "Jorhat Town")]
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


def _distance_to_km(distance) -> float | None:
    if isinstance(distance, bool) or distance is None:
        return None
    try:
        parsed = float(distance)
    except (TypeError, ValueError):
        match = re.search(r"\d+(?:\.\d+)?", str(distance))
        if not match:
            return None
        parsed = float(match.group())
    return parsed if math.isfinite(parsed) and parsed > 0 else None


def _fare_class_for_train(train_type: str | None) -> str | None:
    kind = (train_type or "").lower().replace("-", " ")
    if any(token in kind for token in ("rajdhani", "duronto")):
        return "3A"
    if any(token in kind for token in ("shatabdi", "vande bharat")):
        return "CC"
    if any(token in kind for token in ("passenger", "local")):
        return "2S"
    if any(token in kind for token in ("express", "superfast", "mail")):
        return "SL"
    return None


def _train_price_fields(train_type: str | None, distance_km: float | None) -> dict:
    price_class = _fare_class_for_train(train_type)
    if distance_km is None or price_class is None:
        return {
            "price_num": None,
            "price": None,
            "price_class": price_class,
            "price_status": "UNAVAILABLE",
        }

    price_num = int(round(distance_km * RAIL_BASE_FARE_RATES_INR_PER_KM[price_class]))
    if price_num <= 0:
        return {
            "price_num": None,
            "price": None,
            "price_class": price_class,
            "price_status": "UNAVAILABLE",
        }
    return {
        "price_num": price_num,
        "price": f"₹{price_num:,}",
        "price_class": price_class,
        "price_status": "ESTIMATED",
        "price_note": "Estimate excludes reservation fees and superfast surcharges.",
    }


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
      5. Geocoded nearest major railheads from the bundled station table

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

    return _nearest_railheads(raw)


# ---------------------------------------------------------------------------
# RailRadar API — fetch layer
# ---------------------------------------------------------------------------

def _geocode_indian_place(place: str) -> tuple[float, float] | None:
    """Return cached Open-Meteo coordinates for an Indian place, or None."""
    clean = normalize_location_text(place)
    if not clean:
        return None

    with _GEOCODE_CACHE_LOCK:
        if clean in _GEOCODE_CACHE:
            return _GEOCODE_CACHE[clean]

        coordinates = None
        try:
            response = requests.get(
                GEOCODING_URL,
                params={"name": clean, "count": 1},
                timeout=8,
            )
            response.raise_for_status()
            payload = response.json()
            results = payload.get("results") if isinstance(payload, dict) else None
            result = results[0] if isinstance(results, list) and results else None
            country_code = result.get("country_code") if isinstance(result, dict) else None
            if isinstance(country_code, str) and country_code.upper() == "IN":
                latitude = result.get("latitude")
                longitude = result.get("longitude")
                if (
                    isinstance(latitude, (int, float))
                    and not isinstance(latitude, bool)
                    and isinstance(longitude, (int, float))
                    and not isinstance(longitude, bool)
                    and -90 <= latitude <= 90
                    and -180 <= longitude <= 180
                ):
                    coordinates = (float(latitude), float(longitude))
        except (
            requests.exceptions.RequestException,
            ValueError,
            TypeError,
            AttributeError,
        ):
            pass

        _GEOCODE_CACHE[clean] = coordinates
        return coordinates


@lru_cache(maxsize=1)
def _load_station_table() -> tuple[tuple[str, str, float, float], ...]:
    station_path = Path(__file__).resolve().parents[1] / "data" / "stations.json"
    with station_path.open(encoding="utf-8") as station_file:
        records = json.load(station_file)
    if not isinstance(records, list):
        raise ValueError("Bundled railway station data must be a JSON array")

    stations = []
    for record in records:
        if not isinstance(record, dict):
            continue
        code = record.get("code")
        name = record.get("name")
        latitude = record.get("lat")
        longitude = record.get("lon")
        if (
            isinstance(code, str)
            and isinstance(name, str)
            and isinstance(latitude, (int, float))
            and isinstance(longitude, (int, float))
        ):
            stations.append((code, name, float(latitude), float(longitude)))
    return tuple(stations)


def _haversine_km(latitude: float, longitude: float, station_lat: float, station_lon: float) -> float:
    earth_radius_km = 6371.0
    lat1 = math.radians(latitude)
    lat2 = math.radians(station_lat)
    delta_lat = lat2 - lat1
    delta_lon = math.radians(station_lon - longitude)
    haversine = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    )
    return 2 * earth_radius_km * math.asin(math.sqrt(haversine))


def _nearest_railheads(place: str) -> list[tuple[str, str]]:
    """Resolve an unknown Indian place to its three nearest major railheads."""
    clean = normalize_location_text(place)
    if not clean:
        return []

    with _RAILHEAD_CACHE_LOCK:
        if clean in _RAILHEAD_CACHE:
            return list(_RAILHEAD_CACHE[clean])

    coordinates = _geocode_indian_place(clean)
    if coordinates is None:
        with _RAILHEAD_CACHE_LOCK:
            _RAILHEAD_CACHE[clean] = []
        return []

    latitude, longitude = coordinates
    ranked = sorted(
        (
            _haversine_km(latitude, longitude, station_lat, station_lon),
            code,
            name,
        )
        for code, name, station_lat, station_lon in _load_station_table()
    )
    nearest = [(code, name) for _, code, name in ranked[:3]]
    with _RAILHEAD_CACHE_LOCK:
        _RAILHEAD_CACHE[clean] = nearest
        if ranked:
            distance, code, name = ranked[0]
            _RAILHEAD_DISTANCE_KM[clean] = (code, name, distance)
    return list(nearest)

def _fetch_between(
    origin_code: str,
    destination_code: str,
    date: str,
    by_city: bool = False,
    limit: int = 10,
) -> tuple[list, str | None]:
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

    Returns (trains, error); an empty train list without an error means the
    request succeeded but no trains were found.
    """
    if not RAILRADAR_API_KEY:
        return [], "Train search unavailable: RailRadar API key is missing."

    url = f"{RAILRADAR_BASE_URL}/{origin_code}/{destination_code}"
    headers = {"Authorization": f"Bearer {RAILRADAR_API_KEY}"}
    params = {
        "date":   date,
        "byCity": "true" if by_city else "false",
    }

    try:
        response = requests.get(url, headers=headers, params=params, timeout=30)
    except requests.exceptions.Timeout:
        return [], "Network/timeout error while contacting RailRadar: request timed out."
    except requests.exceptions.RequestException:
        return [], "Network/timeout error while contacting RailRadar."

    if response.status_code in (401, 403):
        reason = "Unauthorized" if response.status_code == 401 else "Forbidden"
        return [], f"RailRadar rejected the API key (HTTP {response.status_code} {reason})."
    if response.status_code == 429:
        return [], "RailRadar rate limit reached (HTTP 429)."
    if response.status_code >= 500:
        return [], f"RailRadar upstream error (HTTP {response.status_code})."
    if response.status_code >= 400:
        return [], f"RailRadar upstream error (HTTP {response.status_code})."

    try:
        data = response.json()
    except ValueError:
        return [], "RailRadar upstream error: response was not valid JSON."

    if not isinstance(data, dict) or not data.get("success"):
        return [], "RailRadar upstream error: response did not contain successful train data."

    response_data = data.get("data") or {}
    if not isinstance(response_data, dict):
        return [], "RailRadar upstream error: train data had an unexpected format."
    trains_payload = response_data.get("trains", [])
    if not isinstance(trains_payload, list):
        return [], "RailRadar upstream error: train results had an unexpected format."
    from_info = response_data.get("from", {})
    to_info = response_data.get("to", {})
    if not isinstance(from_info, dict) or not isinstance(to_info, dict):
        return [], "RailRadar upstream error: station data had an unexpected format."
    if any(not isinstance(item, dict) for item in trains_payload):
        return [], "RailRadar upstream error: a train result had an unexpected format."

    results = []
    for item in trains_payload[:limit]:
        train_info = item.get("train", {})
        from_sched = item.get("from", {})
        to_sched   = item.get("to", {})
        duration_value = item.get("duration")
        try:
            duration_mins = (
                int(duration_value)
                if not isinstance(duration_value, bool) and str(duration_value).isdigit()
                else None
            )
        except (TypeError, ValueError):
            duration_mins = None
        distance_km = _distance_to_km(item.get("distance"))
        train_type = train_info.get("type")
        result = {
            "train_name":             train_info.get("name", "Unknown Train"),
            "train_number":           train_info.get("number", "N/A"),
            "departure_station":      from_info.get("name", origin_code),
            "arrival_station":        to_info.get("name", destination_code),
            "departure_station_code": from_info.get("code", origin_code),
            "arrival_station_code":   to_info.get("code", destination_code),
            "departure_time":         from_sched.get("departure", "N/A"),
            "arrival_time":           to_sched.get("arrival", "N/A"),
            "duration":               _minutes_to_duration(
                duration_mins if duration_mins is not None else duration_value
            ),
            "duration_mins":          duration_mins,
            "train_type":             train_type,
            "run_days":               train_info.get("runDays"),
            "distance_km":            distance_km,
            "available_classes":      [],   # not provided by this endpoint
            "status":                 "Scheduled",
        }
        result.update(_train_price_fields(train_type, distance_km))
        results.append(result)

    return results, None


def _cached_fetch_between(
    origin_code: str,
    destination_code: str,
    date: str,
    by_city: bool,
    limit: int = 10,
) -> tuple[list, str | None]:
    """Cache one request per station pair, date, and city-expansion mode."""
    key = (origin_code, destination_code, date, by_city)
    with _FETCH_CACHE_LOCK:
        future = _FETCH_CACHE.get(key)
        owner = future is None
        if owner:
            future = Future()
            _FETCH_CACHE[key] = future

    if owner:
        try:
            future.set_result(
                _fetch_between(
                    origin_code,
                    destination_code,
                    date,
                    by_city=by_city,
                    limit=10,
                )
            )
        except BaseException as exc:
            future.set_exception(exc)
            raise

    trains, error = future.result()
    return [dict(train) for train in trains[:limit]], error


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


def _station_call_pairs(origin_candidates: list, destination_candidates: list) -> list:
    """Build the bounded station pairs used by regular and alternate searches."""
    max_calls = 6
    call_pairs = []
    if len(origin_candidates) == 1 and len(destination_candidates) == 1:
        call_pairs.append((origin_candidates[0][0], destination_candidates[0][0], True))
    elif len(origin_candidates) == 1:
        o_code = origin_candidates[0][0]
        for d_code, _ in destination_candidates:
            if len(call_pairs) >= max_calls:
                break
            call_pairs.append((o_code, d_code, False))
    elif len(destination_candidates) == 1:
        d_code = destination_candidates[0][0]
        for o_code, _ in origin_candidates:
            if len(call_pairs) >= max_calls:
                break
            call_pairs.append((o_code, d_code, False))
    else:
        for o_code, _ in origin_candidates:
            for d_code, _ in destination_candidates:
                if len(call_pairs) >= max_calls:
                    break
                call_pairs.append((o_code, d_code, False))
            if len(call_pairs) >= max_calls:
                break
    return call_pairs


def _sort_trains(trains: list) -> list:
    trains.sort(
        key=lambda train: (
            train.get("duration_mins") is None,
            train.get("duration_mins") if train.get("duration_mins") is not None else 0,
        )
    )
    return trains


def search_trains(origin: str, destination: str, date: str, limit: int = 5):
    if not RAILRADAR_API_KEY:
        return (
            "Train search unavailable: RailRadar API key is missing (RAILRADAR_API_KEY).\n"
            "Please add it in your .env file:\n"
            "RAILRADAR_API_KEY=your_railradar_api_key_here"
        )

    origin_candidates = resolve_station_candidates(origin)
    destination_candidates = resolve_station_candidates(destination)

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

    region_candidates = _lookup_region_railheads(normalize_location_text(destination))
    destination_uses_region_railheads = (
        bool(region_candidates) and destination_candidates == region_candidates
    )
    call_pairs = _station_call_pairs(origin_candidates, destination_candidates)

    all_results = []
    seen_routes = set()
    errors = []
    calls_made = 0

    for o_code, d_code, by_city in call_pairs:
        batch, error = _cached_fetch_between(
            o_code, d_code, date, by_city=by_city, limit=limit
        )
        calls_made += 1
        if error:
            errors.append(error)
        for train in batch:
            route_key = (
                str(train.get("train_number") or "").strip(),
                str(train.get("departure_station_code") or "").strip().upper(),
                str(train.get("arrival_station_code") or "").strip().upper(),
            )
            if route_key not in seen_routes:
                seen_routes.add(route_key)
                if destination_uses_region_railheads and (
                    train.get("arrival_station") or ""
                ).strip().casefold() != destination.strip().casefold():
                    train["rail_ends_note"] = (
                        f"Rail ends at {train.get('arrival_station')}; "
                        f"continue by road to {destination}"
                    )
                elif (
                    (railhead := _RAILHEAD_DISTANCE_KM.get(
                        normalize_location_text(destination)
                    ))
                    and train.get("arrival_station_code") == railhead[0]
                ):
                    train["rail_ends_note"] = (
                        f"Rail ends at {railhead[1]}, about {round(railhead[2])} km "
                        f"by road to {destination}"
                    )
                all_results.append(train)

    if not all_results:
        if errors:
            priority = (
                "rejected the API key",
                "rate limit reached",
                "Network/timeout error",
                "upstream error",
            )
            for category in priority:
                matching = next((error for error in errors if category in error), None)
                if matching:
                    return matching
            return errors[0]
        return (
            f"RailRadar returned no trains between '{origin}' and '{destination}' "
            f"on {date} after successfully checking {calls_made} station combination(s)."
        )

    return _sort_trains(all_results)[:limit]


def _search_alternate_day(
    day: str,
    call_pairs: list,
) -> tuple[str, list]:
    all_results = []
    seen_routes = set()
    errors = []
    for origin_code, destination_code, by_city in call_pairs:
        batch, error = _cached_fetch_between(
            origin_code, destination_code, day, by_city=by_city, limit=10
        )
        if error:
            errors.append(error)
        for train in batch:
            route_key = (
                str(train.get("train_number") or "").strip(),
                str(train.get("departure_station_code") or "").strip().upper(),
                str(train.get("arrival_station_code") or "").strip().upper(),
            )
            if route_key not in seen_routes:
                seen_routes.add(route_key)
                all_results.append(train)
    if errors:
        logger.warning(
            "alternate train lookup failed date=%s reason=%s",
            day,
            errors[0].splitlines()[0],
        )
    return day, _sort_trains(all_results)


def find_alternate_dates(
    origin: str,
    destination: str,
    date: str,
    days: int = 7,
) -> list[dict]:
    """Return dates with trains among the days after the requested date."""
    if days <= 0 or not RAILRADAR_API_KEY:
        return []

    start_date = datetime.strptime(date, "%Y-%m-%d").date()
    origin_candidates = resolve_station_candidates(origin)
    destination_candidates = resolve_station_candidates(destination)
    if not origin_candidates or not destination_candidates:
        return []

    call_pairs = _station_call_pairs(origin_candidates, destination_candidates)
    dates = [start_date + timedelta(days=offset) for offset in range(1, days + 1)]
    results_by_date = {}
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(_search_alternate_day, day.isoformat(), call_pairs): day
            for day in dates
        }
        for future in as_completed(futures):
            day_string, trains = future.result()
            results_by_date[day_string] = trains

    alternates = []
    for day in dates:
        trains = results_by_date[day.isoformat()]
        if trains:
            alternates.append(
                {
                    "date": day.isoformat(),
                    "weekday": day.strftime("%a"),
                    "trains": [
                        {
                            "number": train.get("train_number"),
                            "name": train.get("train_name"),
                            "departure_time": train.get("departure_time"),
                        }
                        for train in trains
                    ],
                }
            )
    return alternates


if __name__ == "__main__":
    print("Fetching trains...\n")

    train_results = search_trains("Delhi", "Agra", "2026-10-09", limit=5)

    if isinstance(train_results, str):
        print(train_results)
    else:
        for train in train_results:
            print(format_train(train))
            print("-" * 20)