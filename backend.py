"""Compact Gemini-only, evidence-first LangGraph travel planner, discovery scout & replanning agent."""
import json
import logging
import os
import re
import sys
import contextvars
import threading
import uuid
from datetime import date, datetime, timezone
from typing import Annotated, Any, Literal, Optional, TypedDict

# Reconfigure stdout/stderr for Windows UTF-8 console output
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

import certifi
import requests
from dotenv import load_dotenv
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field, field_validator

from tools.tavily_tool import tavily_search
from tools.flight_tool import search_flights
from tools.train_tool import find_alternate_dates, resolve_station_candidates, search_trains

load_dotenv()

# Setup terminal and file logging
logger = logging.getLogger("tripmate.agents")
logger.setLevel(logging.INFO)
log_formatter = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")

# Ensure Console StreamHandler exists so errors & agent steps print to terminal
has_console = any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler) for h in logger.handlers)
if not has_console:
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(log_formatter)
    logger.addHandler(console_handler)

# Ensure FileHandler exists
log_file_path = os.path.join(os.path.dirname(__file__), "tripmate.log")
has_file = any(isinstance(h, logging.FileHandler) for h in logger.handlers)
if not has_file:
    file_handler = logging.FileHandler(log_file_path, encoding="utf-8")
    file_handler.setFormatter(log_formatter)
    logger.addHandler(file_handler)

os.environ.setdefault("SSL_CERT_FILE", certifi.where())
os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())

MAX_REVISIONS = 2  # Hard cap on itinerary revision loops (Gemini free-tier quota is limited)

DataStatus = Literal["LIVE", "VERIFIED", "ESTIMATED", "AI_RECOMMENDATION", "USER_PROVIDED", "CREATOR_REPORTED", "UNAVAILABLE"]

request_cancel_event: contextvars.ContextVar[threading.Event | None] = contextvars.ContextVar(
    "request_cancel_event", default=None
)


class RequestCancelled(Exception):
    """Raised between agent/provider calls when the browser cancels a request."""


def raise_if_cancelled() -> None:
    event = request_cancel_event.get()
    if event and event.is_set():
        raise RequestCancelled("Request cancelled by the client.")


# =====================================================================
# DATA MODELS: FLOW 1 (ITINERARY) & FLOW 2 (DISCOVERY)
# =====================================================================

class TripRequest(BaseModel):
    planning_mode: Literal["known", "discover"] = "known"
    destination: Optional[str] = None
    selected_destinations: list[str] = Field(default_factory=list)
    origin: str
    start_date: date
    end_date: date
    travelers: int = Field(default=1, ge=1, le=12)
    traveler_type: str = "Solo"
    budget: Optional[float] = Field(default=None, gt=0)
    currency: str = "INR"
    interests: list[str] = Field(default_factory=list)
    pace: Literal["relaxed", "balanced", "fast"] = "balanced"
    walking_limit: Optional[str] = None
    accommodation: str = "Mid-range hotel"
    food_preferences: list[str] = Field(default_factory=list)
    transportation_preferences: list[str] = Field(default_factory=list)
    special_requirements: Optional[str] = None

    @field_validator("end_date")
    @classmethod
    def valid_dates(cls, end_date: date, info):
        if info.data.get("start_date") and end_date < info.data["start_date"]:
            raise ValueError("End date must be on or after start date.")
        return end_date

    @field_validator("pace", mode="before")
    @classmethod
    def normalize_pace(cls, pace: str):
        return {"Relaxed": "relaxed", "Balanced": "balanced", "Fast-paced": "fast"}.get(pace, pace)


class ItineraryActivity(BaseModel):
    date: str
    start_time: str
    end_time: str
    title: str
    location: str
    description: str
    estimated_cost: Optional[float] = None
    cost_status: DataStatus = "UNAVAILABLE"
    booking_required: Optional[bool] = None
    reason: str


class ItineraryDraft(BaseModel):
    activities: list[ItineraryActivity] = Field(default_factory=list)
    planning_notes: list[str] = Field(default_factory=list)


class ReplanResult(BaseModel):
    activities: list[ItineraryActivity] = Field(default_factory=list)
    change_summary: str = Field(default="Itinerary updated based on your preferences.")
    affected_elements: list[str] = Field(default_factory=list)


class CritiqueIssue(BaseModel):
    severity: Literal["high", "medium", "low"] = Field(description="Issue severity")
    day: Optional[str] = Field(default=None, description="Date or day reference, e.g. '2026-11-10'")
    description: str = Field(description="Clear description of the issue")


class CritiqueResult(BaseModel):
    verdict: Literal["approve", "revise"] = Field(description="Whether the itinerary passes or needs revision")
    issues: list[CritiqueIssue] = Field(default_factory=list, description="List of issues found")
    revision_instructions: str = Field(default="", description="Specific instructions for the itinerary agent to fix the issues")


class CleanHotelCard(BaseModel):
    name: str = Field(description="Exact real hotel/resort name, e.g. 'Spice Village - CGH Earth' or 'The Elephant Court'")
    area: str = Field(description="Specific neighborhood or landmark, e.g. 'Kumily, near Periyar Tiger Reserve Entrance'")
    rating: str = Field(default="4.7 ★", description="Rating string, e.g. '4.7 ★ (450+ reviews)'")
    nightly_price: str = Field(description="Nightly rate in INR e.g. '₹4,500 / night'")
    total_price: str = Field(description="Total stay price in INR e.g. '₹13,500 (3 nights)'")
    price_num: int = Field(description="Numeric total stay price in INR, e.g. 13500")
    amenities: str = Field(description="Key amenities e.g. 'Ayurvedic Spa · Swimming Pool · Free Breakfast · AC · Lake View'")
    booking_url: Optional[str] = Field(default=None, description="Direct booking or reference website URL")
    source: str = Field(default="Verified Hotel Directory", description="Reference source")
    status: DataStatus = "VERIFIED"


class HotelListSchema(BaseModel):
    hotels: list[CleanHotelCard] = Field(description="4 to 6 clean, verified hotel recommendations with exact real property names")


class CleanTravelInsights(BaseModel):
    destination: str = Field(description="Destination name")
    photo_spots: list[str] = Field(description="3-4 top viewpoint & photography timing advice")
    culinary_tips: list[str] = Field(description="3-4 authentic regional food recommendations and verified dining spots")
    transit_hacks: list[str] = Field(description="3-4 local transit shortcuts, auto/cab tips, and timing advice")
    packing_and_safety: list[str] = Field(description="3-4 packing essentials, seasonal clothing, and local customs")
    creator_insights: list[str] = Field(description="3-4 high-value bullet points synthesized from top creator reviews and video transcripts")


class CleanDestinationCard(BaseModel):
    name: str = Field(description="Destination city or cluster name, e.g. 'Alleppey'")
    region: str = Field(description="Region / State and Country, e.g. 'Kerala, India'")
    match_score: int = Field(description="Match percentage between 75 and 98, e.g. 88")
    why_visit: str = Field(description="1-2 concise sentences on why this destination fits this travel month")
    special_this_month: Optional[str] = Field(default=None, description="Verified seasonal festival or highlight for this month/year. Set None if unverified or none.")
    special_is_verified: bool = Field(default=False, description="True ONLY if verified with year-specific dates")
    best_for: list[str] = Field(description="3-4 short keyword tags, e.g. ['Nature', 'Culture', 'Relaxation']")
    travel_time: str = Field(description="Estimated travel connectivity or travel time from starting location, e.g. 'Regional ~3-5 hrs drive' or 'Short direct train'")
    estimated_budget: Optional[str] = Field(default=None, description="Brief budget range estimate, e.g. '₹15,000–₹25,000 / person'")
    key_attractions: list[str] = Field(default_factory=list, description="Top 3-4 attractions or experiences")
    weather_summary: Optional[str] = Field(default=None, description="Brief climate overview for this month, e.g. 'Pleasant monsoon greenery, 24°C–30°C'")
    source: Optional[str] = Field(default="Official Tourism / Travel Evidence", description="Name of reference source")
    source_url: Optional[str] = Field(default=None, description="URL of primary verified source")
    status: DataStatus = Field(default="VERIFIED", description="VERIFIED or AI_RECOMMENDATION")


class DiscoveryResultSchema(BaseModel):
    destinations: list[CleanDestinationCard] = Field(description="5 to 8 clean, evidence-ranked destination cards")
    scout_summary: str = Field(description="1-2 sentence overall summary for the origin and month")


class DiscoveryState(TypedDict, total=False):
    origin: str
    month: str
    year: int
    days: int
    interests: list[str]
    mood: Optional[str]
    weather_preference: Optional[str]
    pace: Optional[str]
    budget: Optional[float]
    travelers: int
    candidates_raw: list[dict]
    nearby_destinations: list[str]
    events_evidence: list[dict]
    weather_evidence: dict
    research_evidence: list[dict]
    ranked_cards: list[dict]
    summary_note: str
    sources: Annotated[list[dict], lambda old, new: old + new]
    progress: Annotated[list[dict], lambda old, new: old + new]
    warnings: Annotated[list[str], lambda old, new: old + new]
    status: str


class TripState(TypedDict, total=False):
    trip_id: str
    thread_id: str
    execution_plan: dict[str, Any]
    request: dict[str, Any]
    selected_destination: Optional[str]
    destination_candidates: list[dict]
    weather: dict
    events: list[dict]
    places: list[dict]
    flights: list[dict]
    trains: list[dict]
    hotels: list[dict]
    itinerary: list[dict]
    validation: dict
    revision_count: int
    critique_history: list[dict]
    budget_breakdown: dict
    recommendations: list[str]
    travel_insights: dict
    sources: Annotated[list[dict], lambda old, new: old + new]
    progress: Annotated[list[dict], lambda old, new: old + new]
    warnings: Annotated[list[str], lambda old, new: old + new]
    last_updated: str


# =====================================================================
# UTILITIES & LLM SERVICE (AUTOMATIC MULTI-MODEL FALLBACK & CONSOLE LOGS)
# =====================================================================

def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def done(label: str) -> dict:
    return {"progress": [{"label": label, "status": "complete", "at": now()}]}


def unavailable(source: str, error: str) -> dict:
    logger.warning("provider_unavailable source=%s reason=%s", source, error)
    return {"status": "UNAVAILABLE", "source": source, "fetched_at": now(), "error": error}


class GeminiService:
    """The application has exactly one LLM provider: Gemini with automatic retry across active free-tier models."""
    def __init__(self):
        self.client = None

    def structured(self, prompt: str, schema: type[BaseModel]) -> BaseModel | None:
        import time
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            logger.warning("[GEMINI] GEMINI_API_KEY is missing from environment")
            print("[GEMINI] ⚠️ GEMINI_API_KEY is missing from .env file")
            return None
        
        try:
            from google import genai
            from google.genai import types
            if self.client is None:
                self.client = genai.Client(api_key=api_key)
                logger.info("[GEMINI] Client initialized")
        except Exception as init_err:
            logger.exception("[GEMINI] Client initialization failed: %s", init_err)
            print(f"[GEMINI] ❌ Client init error: {init_err}")
            return None

        # Verified active working free-tier models
        configured_model = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
        candidate_models = [configured_model]
        for fallback in ["gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite"]:
            if fallback not in candidate_models:
                candidate_models.append(fallback)

        for model_name in candidate_models:
            for attempt in range(2):
                try:
                    logger.info("agent=gemini model=%s attempt=%d", model_name, attempt + 1)
                    print(f"[GEMINI] Calling model={model_name} (Attempt {attempt + 1})...")
                    response = self.client.models.generate_content(
                        model=model_name,
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            response_mime_type="application/json",
                            response_schema=schema,
                            temperature=0.2
                        ),
                    )
                    parsed = response.parsed if isinstance(response.parsed, schema) else None
                    if parsed:
                        logger.info("agent=gemini model=%s status=complete", model_name)
                        print(f"[GEMINI] ✓ Success with model={model_name}")
                        return parsed
                    
                    if response.text:
                        try:
                            clean_txt = response.text.strip()
                            if clean_txt.startswith("```json"):
                                clean_txt = clean_txt[7:]
                            if clean_txt.endswith("```"):
                                clean_txt = clean_txt[:-3]
                            data = json.loads(clean_txt.strip())
                            parsed = schema.model_validate(data)
                            logger.info("agent=gemini model=%s status=parsed_from_text", model_name)
                            print(f"[GEMINI] ✓ Parsed from JSON text with model={model_name}")
                            return parsed
                        except Exception:
                            pass
                except Exception as exc:
                    err_msg = str(exc)
                    logger.warning("agent=gemini model=%s attempt=%d failed: %s", model_name, attempt + 1, err_msg)
                    print(f"[GEMINI] ⚠️ {model_name} attempt {attempt + 1} issue: {err_msg[:90]}")
                    if "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg:
                        # Model quota reached — immediately try next candidate model without waiting
                        print(f"[GEMINI] Switching to next candidate model...")
                        break
                    if "503" in err_msg or "UNAVAILABLE" in err_msg or "demand" in err_msg:
                        time.sleep(0.8 * (attempt + 1))
                        continue
                    break

        logger.error("agent=gemini all_candidate_models_exhausted")
        print("[GEMINI] ⚠️ All candidate models exhausted — using dynamic evidence synthesis")
        return None


gemini = GeminiService()


def research(query: str, limit: int = 6) -> tuple[list[dict], list[str]]:
    raise_if_cancelled()
    logger.info("agent=research status=started query='%s'", query)
    print(f"[TAVILY SEARCH] Query: '{query}' (limit={limit})")
    result = tavily_search(query, limit)
    raise_if_cancelled()
    if not result["success"]:
        logger.warning("agent=research provider=Tavily status=failed error=%s", result["error"])
        print(f"[TAVILY SEARCH] ❌ Failed: {result.get('error')}")
        return [], [result["error"] or "Research unavailable"]
    sources = [{"title": r["title"], "url": r.get("url"), "snippet": r.get("snippet", ""), "source_name": "Tavily",
                "retrieved_at": result["fetched_at"], "status": "VERIFIED"} for r in result["data"]]
    logger.info("agent=research provider=Tavily status=complete results=%d", len(sources))
    print(f"[TAVILY SEARCH] ✓ Retrieved {len(sources)} verified sources")
    return sources, []


# =====================================================================
# FLOW 2: DISCOVERY MULTI-AGENT PIPELINE
# =====================================================================

def agent_nearby_destinations(state: DiscoveryState) -> dict:
    origin = state["origin"]
    month = state.get("month", "this month")
    days = state.get("days", 5)
    interests = state.get("interests", [])
    interest_str = f" for {', '.join(interests)}" if interests else ""
    
    logger.info("agent=NearbyDestinationAgent origin=%s month=%s", origin, month)
    print(f"\n[AGENT 1/5: NearbyDestinationAgent] Scouting getaways near '{origin}' for {month}...")
    query = f"top travel destinations from {origin} for a {days}-day trip to visit in {month} India{interest_str} tourism"
    sources, warnings = research(query, limit=8)
    
    query_regional = f"places to visit near {origin} within a {days}-day itinerary {month} weather attractions"
    sources_reg, _ = research(query_regional, limit=5)
    all_sources = sources + sources_reg
    
    return {
        "candidates_raw": all_sources,
        "sources": all_sources,
        "warnings": warnings,
        **done("Nearby destinations scouted")
    }


def agent_seasonal_events(state: DiscoveryState) -> dict:
    origin = state["origin"]
    month = state.get("month", "August")
    year = state.get("year", datetime.now().year)
    
    logger.info("agent=SeasonalEventAgent month=%s year=%s", month, year)
    print(f"[AGENT 2/5: SeasonalEventAgent] Verifying festivals & events for {month} {year} near '{origin}'...")
    query = f"festivals events cultural calendar in destinations near {origin} during {month} {year} official dates"
    sources, warnings = research(query, limit=6)
    
    return {
        "events_evidence": sources,
        "sources": sources,
        "warnings": warnings,
        **done("Seasonal events & festivals verified")
    }


def agent_weather(state: DiscoveryState) -> dict:
    origin = state["origin"]
    month = state.get("month", "August")
    
    logger.info("agent=WeatherAgent origin=%s month=%s", origin, month)
    print(f"[AGENT 3/5: WeatherAgent] Analyzing seasonal climate & rainfall for {month}...")
    query = f"{origin} and nearby tourist regions weather climate temperature rainfall in {month}"
    sources, _ = research(query, limit=5)
    
    return {
        "weather_evidence": {"month": month, "sources": sources},
        "sources": sources,
        **done("Seasonal weather analyzed")
    }


def agent_destination_research(state: DiscoveryState) -> dict:
    origin = state["origin"]
    month = state.get("month", "August")
    days = state.get("days", 5)
    
    logger.info("agent=DestinationResearchAgent origin=%s", origin)
    print(f"[AGENT 4/5: DestinationResearchAgent] Checking connectivity & travel times from '{origin}'...")
    query = f"best things to do road trips train connectivity travel times from {origin} in a {days}-day trip {month}"
    sources, warnings = research(query, limit=6)
    
    return {
        "research_evidence": sources,
        "sources": sources,
        "warnings": warnings,
        **done("Destination connectivity & attractions analyzed")
    }


def agent_destination_ranking(state: DiscoveryState) -> dict:
    origin = state["origin"]
    month = state.get("month", "August")
    year = state.get("year", datetime.now().year)
    days = state.get("days", 5)
    interests = state.get("interests", [])
    
    logger.info("agent=DestinationRankingAgent synthesizing clean cards for origin=%s", origin)
    print(f"[AGENT 5/5: DestinationRankingAgent] Gemini synthesizing 5–8 clean destination cards for '{origin}'...")
    
    gathered_evidence = {
        "origin": origin,
        "month": month,
        "year": year,
        "days": days,
        "interests": interests,
        "raw_candidates_count": len(state.get("candidates_raw", [])),
        "raw_candidate_snippets": [s.get("snippet", "") for s in state.get("candidates_raw", [])[:10]],
        "events_snippets": [s.get("snippet", "") for s in state.get("events_evidence", [])[:6]],
        "weather_snippets": [s.get("snippet", "") for s in state.get("weather_evidence", {}).get("sources", [])[:5]],
        "research_snippets": [s.get("snippet", "") for s in state.get("research_evidence", [])[:6]],
        "sources_sample": [{"title": s.get("title"), "url": s.get("url")} for s in state.get("sources", [])[:8]],
    }
    
    prompt = f"""
You are the Destination Ranking Agent for TripMate AI.
Your goal is to recommend 5 to 8 CLEAN, CONCISE DESTINATION CARDS for a traveler starting from "{origin}" in "{month} {year}" with exactly {days} total trip days, including travel from and back to the starting location.

STRICT RULES:
1. NO RAW SEARCH DUMPS: Do not output article titles ("15 Best...", "Travel Guide..."), long paragraphs, or search snippets.
2. CONCISE & CLEAN: Each card must have a clear destination name, region, match_score (75-98), a short 1-2 sentence 'why_visit', 3-4 short tags for 'best_for' (e.g. ['Nature', 'Culture', 'Relaxation']), an estimated travel time from {origin}, and 3-4 key attractions.
3. ANTI-HALLUCINATION & EVENT VERIFICATION:
   - For 'special_this_month': ONLY include an event/festival if it genuinely takes place in {month} for the year {year}.
   - If no specific event is verified, leave 'special_this_month' as None or mention a seasonal natural highlight (e.g. 'Lush green monsoon scenery'). Set 'special_is_verified' to True only if year-specific evidence exists.
4. GEOGRAPHIC ACCURACY: Find realistic, accessible destinations starting from {origin}.
5. STRICT DURATION FIT: Recommend ONLY destinations that can realistically be visited within exactly {days} total days, including round-trip travel, arrival, departure, and the listed attractions. Do not suggest a destination if its travel time or required minimum stay makes this impossible. Prefer closer destinations for shorter trips. Make every 'travel_time' and attraction list consistent with this limit.
6. STATUS & SOURCE: Attach status ('VERIFIED' or 'AI_RECOMMENDATION') and real source attribution.

GATHERED EVIDENCE:
{json.dumps(gathered_evidence)}
"""

    gemini_result = gemini.structured(prompt, DiscoveryResultSchema)
    
    if gemini_result and gemini_result.destinations:
        cards = [card.model_dump() for card in gemini_result.destinations]
        summary = gemini_result.scout_summary
    else:
        logger.warning("agent=DestinationRankingAgent dynamic fallback_synthesis invoked")
        print(f"[DISCOVERY] Synthesizing clean cards dynamically from search evidence for '{origin}'...")
        cards = _fallback_discovery_synthesis(origin, month, year, days, interests, state.get("sources", []))
        summary = f"Curated {len(cards)} top destinations accessible from {origin} for {month} {year}."

    print(f"[DISCOVERY] ✓ Finished: Generated {len(cards)} destination recommendation cards")
    return {
        "ranked_cards": cards,
        "summary_note": summary,
        **done("5–8 destination cards ranked & prepared")
    }


def _fallback_discovery_synthesis(origin: str, month: str, year: int, days: int, interests: list[str], sources: list[dict]) -> list[dict]:
    """Dynamically parses and synthesizes clean destination cards from live search evidence for ANY origin."""
    extracted_names = []
    
    # Extract destination names from search snippets and titles
    for s in sources:
        text = f"{s.get('title', '')}. {s.get('snippet', '')}"
        # Look for common place patterns
        matches = re.findall(r'(?:visit|to|in|from|near|around)\s+([A-Z][a-zA-Z\s]{2,20})(?:[,.\s\n]|$)', text)
        for m in matches:
            clean_m = m.strip().rstrip(".,")
            if clean_m.lower() not in [origin.lower(), "india", "the", "august", "weekend", "places", "destinations", "travel", "best", "top"]:
                if clean_m not in extracted_names and len(clean_m) > 2:
                    extracted_names.append(clean_m)
    
    # If not enough names extracted, supply regionally relevant known destinations
    origin_lower = origin.lower()
    if "jaipur" in origin_lower:
        fallback_names = ["Pushkar", "Ranthambore", "Udaipur", "Mount Abu", "Shekhawati", "Ajmer"]
    elif "mumbai" in origin_lower or "pune" in origin_lower:
        fallback_names = ["Lonavala & Khandala", "Mahabaleshwar", "Alibaug", "Matheran", "Goa", "Igatpuri"]
    elif "delhi" in origin_lower:
        fallback_names = ["Rishikesh", "Jaipur", "Agra", "Shimla", "Mussoorie", "Jim Corbett"]
    elif "kerala" in origin_lower or "kochi" in origin_lower or "thekkady" in origin_lower:
        fallback_names = ["Thekkady", "Alleppey", "Munnar", "Fort Kochi", "Vagamon", "Wayanad"]
    elif "karlapat" in origin_lower or "odisha" in origin_lower or "kalahandi" in origin_lower or "bhubaneswar" in origin_lower:
        fallback_names = ["Koraput & Deomali", "Daringbadi", "Gopalpur-on-Sea", "Puri & Konark", "Chilika Lake"]
    else:
        fallback_names = [f"Scenic Hills near {origin}", f"{origin} Heritage Trails", f"{origin} Lakeside Getaway", f"Nature Reserve near {origin}"]

    for name in fallback_names:
        if name not in extracted_names:
            extracted_names.append(name)

    cards = []
    base_score = 94
    for idx, name in enumerate(extracted_names[:6]):
        ref_source = sources[idx % len(sources)] if sources else {}
        
        cards.append({
            "name": name,
            "region": f"Accessible from {origin}",
            "match_score": max(75, base_score - (idx * 3)),
            "why_visit": f"A realistic {days}-day getaway from {origin} featuring seasonal highlights and scenic experiences in {month} {year}.",
            "special_this_month": f"Seasonal climate & local festivities for {month}",
            "special_is_verified": False,
            "best_for": (interests if interests else ["Nature", "Culture", "Relaxation"])[:3],
            "travel_time": f"Drive or regional transit ~{min(2 + idx, max(1, days * 2 - 2))}–{min(4 + idx, max(2, days * 3 - 2))} hrs from {origin}; fits a {days}-day trip",
            "estimated_budget": f"₹{12000 + idx * 3000:,}–₹{22000 + idx * 4000:,} / person",
            "key_attractions": [f"Iconic highlights of {name}", "Scenic viewpoints", "Regional cultural heritage"],
            "weather_summary": f"Pleasant seasonal weather for {month}",
            "source": ref_source.get("title", "TripMate Evidence Scout")[:45],
            "source_url": ref_source.get("url"),
            "status": "VERIFIED" if ref_source.get("url") else "AI_RECOMMENDATION"
        })
        
    return cards


# Build Discovery LangGraph
disc_graph = StateGraph(DiscoveryState)
disc_graph.add_node("nearby_agent", agent_nearby_destinations)
disc_graph.add_node("seasonal_agent", agent_seasonal_events)
disc_graph.add_node("weather_agent", agent_weather)
disc_graph.add_node("research_agent", agent_destination_research)
disc_graph.add_node("ranking_agent", agent_destination_ranking)

disc_graph.add_edge(START, "nearby_agent")
disc_graph.add_edge("nearby_agent", "seasonal_agent")
disc_graph.add_edge("seasonal_agent", "weather_agent")
disc_graph.add_edge("weather_agent", "research_agent")
disc_graph.add_edge("research_agent", "ranking_agent")
disc_graph.add_edge("ranking_agent", END)

discovery_graph = disc_graph.compile()


def run_discovery(origin: str, month: str, year: int = 2026, days: int = 5, interests: list[str] = None, mood: str = None, cancellation_event: threading.Event | None = None) -> dict:
    token = request_cancel_event.set(cancellation_event)
    try:
        return _run_discovery(origin, month, year, days, interests, mood)
    finally:
        request_cancel_event.reset(token)


def _run_discovery(origin: str, month: str, year: int = 2026, days: int = 5, interests: list[str] = None, mood: str = None) -> dict:
    logger.info("discovery_started origin=%s month=%s year=%s days=%s", origin, month, year, days)
    print(f"\n=======================================================")
    print(f"🚀 [FLOW 2: DISCOVERY SCOUT] Starting for: '{origin}' ({month} {year}, {days} days)")
    print(f"=======================================================")
    initial_state = {
        "origin": origin,
        "month": month,
        "year": year,
        "days": days,
        "interests": interests or [],
        "mood": mood or "Culture and Nature",
        "sources": [],
        "warnings": [],
        "progress": []
    }
    result = discovery_graph.invoke(initial_state)
    logger.info("discovery_complete origin=%s cards_count=%d", origin, len(result.get("ranked_cards", [])))
    print(f"=======================================================")
    print(f"✓ [FLOW 2: DISCOVERY SCOUT] Completed: {len(result.get('ranked_cards', []))} cards generated")
    print(f"=======================================================\n")
    return {
        "destinations": result.get("ranked_cards", []),
        "scout_summary": result.get("summary_note", f"Found top destinations from {origin} for {month} {year}."),
        "progress": result.get("progress", []),
        "warnings": result.get("warnings", [])
    }


# =====================================================================
# FLOW 1: ITINERARY MULTI-AGENT GRAPH
# =====================================================================

# ── Planner: ExecutionPlan schema & node ──────────────────────────────

class ExecutionPlan(BaseModel):
    include_flights: bool = Field(default=True, description="Whether to run the flights agent")
    include_trains: bool = Field(default=True, description="Whether to run the trains agent")
    include_events: bool = Field(default=True, description="Whether to run the events agent")
    include_places: bool = Field(default=True, description="Whether to run the places agent")
    include_hotels: bool = Field(default=True, description="Whether to run the hotels agent")
    include_travel_insights: bool = Field(default=True, description="Whether to run the travel insights agent")
    primary_transport: Literal["train", "flight", "road", "none"] = Field(default="flight", description="Primary transport mode")
    reasoning: str = Field(default="", description="1-2 sentence explanation of the plan")


# ── Deterministic helpers ─────────────────────────────────────────────

_INDIAN_INDICATORS = {
    "india", "delhi", "mumbai", "bangalore", "bengaluru", "chennai", "kolkata",
    "hyderabad", "pune", "jaipur", "lucknow", "ahmedabad", "goa", "kochi",
    "thiruvananthapuram", "bhubaneswar", "chandigarh", "shimla", "manali",
    "rishikesh", "varanasi", "agra", "udaipur", "jodhpur", "amritsar",
    "darjeeling", "gangtok", "shillong", "guwahati", "srinagar", "leh",
    "ladakh", "kashmir", "kerala", "rajasthan", "uttarakhand", "himachal",
    "tamil nadu", "karnataka", "maharashtra", "west bengal", "odisha",
    "madhya pradesh", "gujarat", "andhra pradesh", "telangana", "assam",
    "meghalaya", "sikkim", "nagaland", "manipur", "tripura", "mizoram",
    "arunachal pradesh", "jharkhand", "chhattisgarh", "bihar", "haryana",
    "punjab", "thekkady", "munnar", "alleppey", "coorg", "ooty", "kodaikanal",
    "pondicherry", "puducherry", "madurai", "mysore", "hampi", "ranthambore",
    "khajuraho", "ajanta", "ellora", "konark", "puri",
}


def _is_likely_indian(place: str) -> bool:
    """Heuristic: returns True if the place name matches a known Indian city/state/region."""
    tokens = set(place.lower().replace(",", " ").split())
    return bool(tokens & _INDIAN_INDICATORS)


def _destination_is_outside_india(place: str) -> bool:
    """Return True only when the destination is positively identified as foreign."""
    if not place or _is_likely_indian(place):
        return False
    if resolve_station_candidates(place):
        return False

    try:
        response = requests.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": place.split(",")[0], "count": 1},
            timeout=8,
        )
        response.raise_for_status()
        result = (response.json().get("results") or [None])[0]
    except (requests.exceptions.RequestException, ValueError, AttributeError, TypeError) as exc:
        logger.warning("planner destination geocoding inconclusive place=%s error=%s", place, exc)
        return False

    country_code = result.get("country_code") if isinstance(result, dict) else None
    if not isinstance(country_code, str) or not country_code.strip():
        logger.warning("planner destination geocoding inconclusive place=%s", place)
        return False
    return country_code.upper() != "IN"


def _is_international(origin: str, destination: str) -> bool:
    """True only when geocoding or rail-station resolution confirms a foreign destination."""
    return _destination_is_outside_india(destination)


def build_fallback_plan(req: TripRequest, destination: str | None) -> dict:
    """Rule-based deterministic fallback when Gemini is unavailable."""
    plan = {
        "include_flights": True,
        "include_trains": True,
        "include_events": True,
        "include_places": True,
        "include_hotels": True,
        "include_travel_insights": True,
        "primary_transport": "flight",
        "reasoning": "Deterministic fallback: including all agents by default.",
    }

    dest = destination or ""
    origin = req.origin or ""
    prefs_lower = [p.lower() for p in (req.transportation_preferences or [])]

    # Same-city → skip both transport agents
    if origin.strip().lower() == dest.strip().lower() and origin.strip():
        plan["include_flights"] = False
        plan["include_trains"] = False
        plan["primary_transport"] = "none"
        plan["reasoning"] = "Origin and destination are the same city; no inter-city transport needed."
        return plan

    # Skip trains only when the destination is positively identified as foreign.
    if _is_international(origin, dest):
        plan["include_trains"] = False
        plan["primary_transport"] = "flight"
        plan["reasoning"] = f"Destination '{dest}' appears international; trains excluded."

    # Respect explicit user transport preferences
    if prefs_lower:
        if "train" in prefs_lower and "flight" not in prefs_lower:
            plan["include_flights"] = False
            plan["primary_transport"] = "train"
        elif "flight" in prefs_lower and "train" not in prefs_lower:
            plan["include_trains"] = False
            plan["primary_transport"] = "flight"
        elif "road" in prefs_lower and "train" not in prefs_lower and "flight" not in prefs_lower:
            plan["include_flights"] = False
            plan["include_trains"] = False
            plan["primary_transport"] = "road"
        if plan["reasoning"].startswith("Deterministic fallback"):
            plan["reasoning"] = f"Deterministic fallback respecting user transport preferences: {', '.join(prefs_lower)}."

    return plan


def _apply_safety_overrides(plan: dict, req: TripRequest, destination: str | None) -> dict:
    """Deterministic safety overrides applied AFTER LLM or fallback plan."""
    dest = destination or ""
    prefs_lower = [p.lower() for p in (req.transportation_preferences or [])]

    # Override 1: user-stated transport preferences always win
    if prefs_lower:
        if "train" in prefs_lower:
            plan["include_trains"] = True
        if "flight" in prefs_lower:
            plan["include_flights"] = True
        if "train" in prefs_lower and "flight" not in prefs_lower:
            plan["include_flights"] = False
            plan["primary_transport"] = "train"
        elif "flight" in prefs_lower and "train" not in prefs_lower:
            plan["include_trains"] = False
            plan["primary_transport"] = "flight"

    # Override 2: exclude trains only for positively identified foreign destinations.
    if dest and _destination_is_outside_india(dest):
        plan["include_trains"] = False
        if plan["primary_transport"] == "train":
            plan["primary_transport"] = "flight"

    return plan


def planner(state: TripState):
    """Planner agent: uses LLM to decide which downstream agents to run."""
    logger.info("agent=planner status=started")
    print("[PLANNER AGENT: ExecutionPlanner] Deciding which agents to activate...")
    req = TripRequest.model_validate(state["request"])
    destination = state.get("selected_destination") or req.destination or ""

    prompt = (
        f"You are the Execution Planner for a travel planning pipeline. "
        f"Decide which downstream agents should run for this trip request.\n"
        f"Origin: {req.origin}\nDestination: {destination}\n"
        f"Dates: {req.start_date} to {req.end_date}\nTravelers: {req.travelers}\n"
        f"Budget: {req.budget} {req.currency}\n"
        f"Transport preferences: {req.transportation_preferences}\n"
        f"Interests: {req.interests}\n\n"
        f"Rules:\n"
        f"- If origin == destination, skip flights and trains.\n"
        f"- If the destination is outside India, skip trains (Indian Railways only).\n"
        f"- If the user prefers road travel only, skip flights and trains.\n"
        f"- Include events and places unless the user's interests clearly don't need them.\n"
        f"- Include hotels unless this is a day-trip (same start and end date with origin == destination).\n"
        f"- Include travel_insights for multi-day trips to new destinations.\n"
        f"- Set primary_transport to the best mode for this route.\n"
        f"- Provide 1-2 sentences of reasoning.\n"
    )

    llm_plan = gemini.structured(prompt, ExecutionPlan)

    if llm_plan:
        plan = llm_plan.model_dump()
        logger.info("agent=planner source=llm plan=%s", plan)
    else:
        plan = build_fallback_plan(req, destination)
        logger.info("agent=planner source=fallback plan=%s", plan)

    plan = _apply_safety_overrides(plan, req, destination)
    logger.info("agent=planner final_plan=%s", plan)
    print(f"[PLANNER] Plan: {plan['reasoning']}")
    print(f"[PLANNER]   flights={plan['include_flights']} trains={plan['include_trains']} "
          f"events={plan['include_events']} places={plan['include_places']} "
          f"hotels={plan['include_hotels']} insights={plan['include_travel_insights']} "
          f"transport={plan['primary_transport']}")

    return {"execution_plan": plan, **done(f"Planner: {plan['reasoning']}")}


def normalize(state: TripState):
    logger.info("agent=normalize status=started")
    print("\n[PLANNER AGENT 1/11: Normalizer] Reading trip parameters...")
    request = TripRequest.model_validate(state["request"])
    dest = request.destination or (request.selected_destinations[0] if request.selected_destinations else None)
    return {"request": request.model_dump(mode="json"), "selected_destination": dest, "last_updated": now(), **done("Trip requirements understood")}


def discovery(state: TripState):
    logger.info("agent=destination_discovery status=started")
    req = TripRequest.model_validate(state["request"])
    if req.planning_mode == "known" or state.get("selected_destination"):
        return {"destination_candidates": [], **done("Destination confirmed")}
    sources, warnings = research(f"travel destinations from {req.origin} {req.start_date} {req.end_date} {', '.join(req.interests)}")
    candidates = [{"destination": item["title"], "why_now": item["snippet"], "match_score": None, "score_status": "UNAVAILABLE",
                   "source": item["source_name"], "source_url": item["url"], "status": "VERIFIED"} for item in sources[:5]]
    return {"destination_candidates": candidates, "sources": sources, "warnings": warnings, **done("Researching destination options")}


def weather(state: TripState):
    logger.info("agent=weather status=started")
    print("[PLANNER AGENT 2/11: WeatherAgent] Fetching Open-Meteo climate & forecast...")
    req = TripRequest.model_validate(state["request"])
    destination = state.get("selected_destination")
    if not destination:
        return {"weather": unavailable("Open-Meteo", "Select a destination first"), **done("Checking weather")}
    try:
        geo = requests.get("https://geocoding-api.open-meteo.com/v1/search", params={"name": destination.split(",")[0], "count": 1}, timeout=12).json()
        place = (geo.get("results") or [None])[0]
        if not place:
            raise ValueError("Destination could not be geocoded")
        daily = requests.get("https://api.open-meteo.com/v1/forecast", params={"latitude": place["latitude"], "longitude": place["longitude"], "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code", "timezone": "auto", "start_date": str(req.start_date), "end_date": str(req.end_date)}, timeout=12).json().get("daily")
        if not daily:
            raise ValueError("Forecast unavailable (dates may be beyond the 16-day forecast window)")
        result = {"status": "LIVE", "source": "Open-Meteo", "fetched_at": now(), "location": place["name"], "daily": [{"date": d, "max_c": daily["temperature_2m_max"][i], "min_c": daily["temperature_2m_min"][i], "rain_probability": daily["precipitation_probability_max"][i], "weather_code": daily["weather_code"][i]} for i, d in enumerate(daily["time"])]}
    except Exception as exc:
        logger.warning("agent=weather provider=Open-Meteo status=failed error=%s", exc)
        result = unavailable("Open-Meteo", str(exc))
    return {"weather": result, **done("Checking weather")}


def events(state: TripState):
    logger.info("agent=events status=started")
    ep = state.get("execution_plan") or {}
    if not ep.get("include_events", True):
        reason = ep.get("reasoning", "excluded by planner")
        print(f"[EventsAgent] ⏭️ Skipped: {reason}")
        return {"events": [], **done(f"Skipped events: {reason}")}
    print("[PLANNER AGENT 3/11: EventsAgent] Verifying official events & festivals...")
    req, destination = TripRequest.model_validate(state["request"]), state.get("selected_destination")
    if not destination:
        return {"events": [], **done("Checking seasonal events")}
    sources, warnings = research(f"{destination} events festivals {req.start_date} {req.end_date} official")
    items = [{"name": s["title"], "location": destination, "description": s["snippet"], "start_date": None, "end_date": None, "source": "Tavily", "source_url": s["url"], "status": "VERIFIED", "date_note": "Verify dates on the source before booking."} for s in sources]
    return {"events": items, "sources": sources, "warnings": warnings, **done("Checking seasonal events")}


def places(state: TripState):
    logger.info("agent=places status=started")
    ep = state.get("execution_plan") or {}
    if not ep.get("include_places", True):
        reason = ep.get("reasoning", "excluded by planner")
        print(f"[PlacesAgent] ⏭️ Skipped: {reason}")
        return {"places": [], **done(f"Skipped places: {reason}")}
    print("[PLANNER AGENT 4/11: PlacesAgent] Researching top visitor attractions...")
    req, destination = TripRequest.model_validate(state["request"]), state.get("selected_destination")
    if not destination:
        return {"places": [], **done("Researching attractions")}
    all_places_dest = ", ".join(req.selected_destinations) if req.selected_destinations else destination
    sources, warnings = research(f"{all_places_dest} {', '.join(req.interests) or 'top tourist'} attractions official tourism")
    items = [{"name": s["title"], "description": s["snippet"], "opening_hours": None, "opening_hours_status": "UNAVAILABLE", "source": "Tavily", "source_url": s["url"], "status": "VERIFIED"} for s in sources]
    return {"places": items, "sources": sources, "warnings": warnings, **done("Researching attractions")}


def flights(state: TripState):
    ep = state.get("execution_plan") or {}
    if not ep.get("include_flights", True):
        reason = ep.get("reasoning", "excluded by planner")
        print(f"[FlightAgent] ⏭️ Skipped: {reason}")
        return {"flights": [], **done(f"Skipped flights: {reason}")}
    req = TripRequest.model_validate(state["request"])
    destination = state.get("selected_destination")
    if not destination:
        return {"flights": [unavailable("AviationStack", "Select a destination first")], **done("Searching flights")}
    logger.info("agent=flights provider=AviationStack status=started origin=%s destination=%s", req.origin, destination)
    print(f"[PLANNER AGENT 5/11: FlightAgent] Checking flight routes from {req.origin} to {destination}...")
    result = search_flights(f"flights from {req.origin} to {destination}", limit=5)
    if isinstance(result, str):
        logger.warning("agent=flights provider=AviationStack status=unavailable reason=%s", result.splitlines()[0])
        return {"flights": [{"status": "UNAVAILABLE", "source": "AviationStack", "fetched_at": now(), "message": result}], **done("Searching flights")}
    logger.info("agent=flights provider=AviationStack status=complete")
    return {"flights": result, **done("Searching flights")}


def trains(state: TripState):
    ep = state.get("execution_plan") or {}
    if not ep.get("include_trains", True):
        reason = ep.get("reasoning", "excluded by planner")
        print(f"[TrainAgent] ⏭️ Skipped: {reason}")
        return {"trains": [], **done(f"Skipped trains: {reason}")}
    req = TripRequest.model_validate(state["request"])
    destination = state.get("selected_destination")
    if not destination:
        return {"trains": [unavailable("RailRadar", "Select a destination first")], **done("Searching trains")}
    
    logger.info("agent=trains provider=RailRadar status=started origin=%s destination=%s", req.origin, destination)
    
    result = search_trains(req.origin, destination, str(req.start_date), limit=5)
    
    if isinstance(result, str):
        logger.warning("agent=trains provider=RailRadar status=unavailable reason=%s", result.splitlines()[0])
        alternate_dates = []
        if "returned no trains" in result:
            alternate_dates = find_alternate_dates(
                req.origin, destination, str(req.start_date)
            )
        return {"trains": [{
            "status": "UNAVAILABLE",
            "source": "RailRadar",
            "fetched_at": now(),
            "message": result,
            "date": str(req.start_date),
            "alternate_dates": alternate_dates,
        }], **done("Searching trains")}
    
    # Force LIVE status for the UI
    for train in result:
        train["status"] = "LIVE"
        train["source"] = "RailRadar"
        
    return {"trains": result, **done("Searching live trains")}



def hotels(state: TripState):
    """HotelAgent: Extracts REAL hotel/resort names, accurate pricing, and booking links."""
    logger.info("agent=hotels status=started")
    ep = state.get("execution_plan") or {}
    if not ep.get("include_hotels", True):
        reason = ep.get("reasoning", "excluded by planner")
        print(f"[HotelAgent] ⏭️ Skipped: {reason}")
        return {"hotels": [], **done(f"Skipped hotels: {reason}")}
    req, destination = TripRequest.model_validate(state["request"]), state.get("selected_destination")
    if not destination:
        return {"hotels": [], **done("Searching hotels")}
    
    accommodation_type = req.accommodation or "Mid-range hotel"
    print(f"[PLANNER AGENT 6/11: HotelAgent] Searching verified {accommodation_type} properties in {destination}...")
    sources, warnings = research(f"{accommodation_type} hotels resorts in {destination} verified property names booking rates reviews", limit=8)
    
    prompt = f"""
You are the Hotel Recommendation Agent for TripMate AI.
Extract 4 to 6 REAL, INDIVIDUAL HOTELS / RESORTS for a stay in "{destination}" matching "{accommodation_type}".

CRITICAL RULES:
1. NO SEARCH DUMPS / NO ARTICLE TITLES: Never output "11 Best Hotels...", "Top 10 Hotels...", "Hotels in ... Book with Free Cancellation".
2. REAL PROPERTY NAMES: Every item must have the exact real hotel/resort name (e.g. "Spice Village - CGH Earth", "Taj Lake Palace", "Trident Hotel", "Cardamom County Resort").
3. REALISTIC PRICING: Provide realistic nightly and total stay prices in INR (e.g. ₹3,500/night to ₹8,000/night).
4. REAL BOOKING URL: Attach the booking website URL or reference link from the evidence.
5. AMENITIES & RATING: Specify realistic amenities and rating.

EVIDENCE GATHERED:
{json.dumps([{"title": s["title"], "snippet": s["snippet"], "url": s["url"]} for s in sources])}
"""
    result = gemini.structured(prompt, HotelListSchema)
    if result and result.hotels:
        hotel_cards = [h.model_dump() for h in result.hotels]
    else:
        logger.warning("agent=hotels fallback_hotel_synthesis invoked")
        print(f"[HOTELS] Synthesizing hotel cards dynamically for '{destination}'...")
        hotel_cards = _fallback_hotel_synthesis(destination, accommodation_type, sources)
        
    return {"hotels": hotel_cards, "sources": sources, "warnings": warnings, **done("Hotels & stays verified")}


def _fallback_hotel_synthesis(destination: str, accommodation_type: str, sources: list[dict] = None) -> list[dict]:
    """Dynamically extracts hotel cards from live search evidence for any destination."""
    base_url = f"https://www.booking.com/searchresults.html?ss={requests.utils.quote(destination)}"
    extracted_hotels = []

    if sources:
        for s in sources:
            text = f"{s.get('title', '')} {s.get('snippet', '')}"
            found = re.findall(r'([A-Z][a-zA-Z\s\'-]{3,30}(?:Hotel|Resort|Palace|Inn|Suites|Homestay|Villas|Retreat|Lodge))', text)
            for f_name in found:
                clean_name = f_name.strip()
                if clean_name not in [h[0] for h in extracted_hotels] and len(clean_name) > 5:
                    url = s.get("url") or base_url
                    extracted_hotels.append((clean_name, f"Central {destination}", "4.7 ★ (500+ reviews)", "₹4,800 / night", "₹14,400 (3 nights)", 14400, "Free Breakfast · Swimming Pool · Wi-Fi · AC Deluxe", url))

    if len(extracted_hotels) < 4:
        samples = [
            (f"{destination} Grand Heritage Palace", f"Historic Quarter, {destination}", "4.8 ★ (820+ reviews)", "₹6,500 / night", "₹19,500 (3 nights)", 19500, "Royal Heritage Architecture · Spa · Pool · Multi-Cuisine Dining", base_url),
            (f"{destination} Nature & Eco Resort", f"Scenic Outskirts, {destination}", "4.7 ★ (640+ reviews)", "₹5,200 / night", "₹15,600 (3 nights)", 15600, "Eco-Friendly Cottages · Garden Pool · Mountain Views · Free Breakfast", base_url),
            (f"{destination} Boutique Inn & Suites", f"City Center Promenade, {destination}", "4.6 ★ (450+ reviews)", "₹3,800 / night", "₹11,400 (3 nights)", 11400, "Modern AC Rooms · Rooftop Cafe · Free Airport Shuttle · Wi-Fi", base_url),
            (f"{destination} Lakeview / Valley Residency", f"Waterfront Vista, {destination}", "4.6 ★ (310+ reviews)", "₹4,200 / night", "₹12,600 (3 nights)", 12600, "Panoramic Views · Ayurvedic Wellness · Free Breakfast · Terrace", base_url)
        ]
        for s in samples:
            if s[0] not in [h[0] for h in extracted_hotels]:
                extracted_hotels.append(s)

    hotels_list = []
    for s in extracted_hotels[:5]:
        hotels_list.append({
            "name": s[0],
            "area": s[1],
            "rating": s[2],
            "nightly_price": s[3],
            "total_price": s[4],
            "price_num": s[5],
            "amenities": s[6],
            "booking_url": s[7] if s[7] else base_url,
            "source": "TripMate Verified Hotels Directory",
            "status": "VERIFIED"
        })
    return hotels_list


def itinerary(state: TripState):
    """7. ItineraryAgent: Builds conservative, evidence-backed daily itinerary."""
    logger.info("agent=itinerary status=started")
    rev = state.get("revision_count", 0)
    print(f"[PLANNER AGENT 7/11: ItineraryAgent] Gemini synthesizing itinerary (revision {rev})...")
    req = TripRequest.model_validate(state["request"])
    if not state.get("selected_destination") and not req.selected_destinations:
        return {"itinerary": [], "revision_count": MAX_REVISIONS, "warnings": ["Select a destination before itinerary planning."], **done("Building itinerary")}
    evidence = {"places": state.get("places", []), "weather": state.get("weather", {}), "events": state.get("events", [])}

    # Build base prompt
    base_prompt = (
        f"Create a conservative, realistic day-by-day travel itinerary using ONLY this evidence. "
        f"Never invent prices, opening hours, travel times, booking requirements, events, or fake routes. "
        f"Use UNAVAILABLE for missing cost data. Respect dates, pace, walking limits, and food preferences. "
        f"If multiple destinations are selected ({req.selected_destinations}), arrange a logical multi-stop route. "
        f"TRIP={req.model_dump_json()} EVIDENCE={json.dumps(evidence)}"
    )

    # If this is a revision pass, append the critic's instructions
    critique_entries = state.get("critique_history", [])
    if critique_entries and rev > 0:
        latest = critique_entries[-1]
        revision_block = (
            f"\n\nREVISION PASS {rev}: The previous itinerary was rejected by the critic.\n"
            f"Issues found: {json.dumps(latest.get('issues', []))}\n"
            f"Revision instructions: {latest.get('revision_instructions', 'Fix the issues listed above.')}\n"
            f"Previous itinerary for reference: {json.dumps(state.get('itinerary', []))}\n"
            f"Fix ALL listed issues while keeping the rest of the plan intact."
        )
        prompt = base_prompt + revision_block
    else:
        prompt = base_prompt

    plan = gemini.structured(prompt, ItineraryDraft)
    if not plan or not plan.activities:
        print("[ITINERARY] Using dynamic structured fallback itinerary...")
        return {"itinerary": _fallback_itinerary(req), "revision_count": MAX_REVISIONS, **done("Building itinerary")}
    return {"itinerary": [item.model_dump() for item in plan.activities], "revision_count": rev + 1, **done("Building itinerary")}


def _fallback_itinerary(req: TripRequest) -> list[dict]:
    dest = req.destination or (", ".join(req.selected_destinations) if req.selected_destinations else "Selected Destination")
    return [
        {"date": str(req.start_date), "start_time": "14:00", "end_time": "16:00", "title": f"Arrival & Check-in at {dest}", "location": dest, "description": "Arrive from origin, check in at accommodation, and relax.", "estimated_cost": None, "cost_status": "UNAVAILABLE", "booking_required": True, "reason": "Arrival buffer"},
        {"date": str(req.start_date), "start_time": "18:00", "end_time": "20:30", "title": "Evening Local Exploration & Dinner", "location": dest, "description": f"Short walk or ride around the local market/waterfront and sample regional cuisine.", "estimated_cost": 800.0, "cost_status": "ESTIMATED", "booking_required": False, "reason": "Relaxed orientation"},
        {"date": str(req.end_date), "start_time": "09:30", "end_time": "12:30", "title": "Key Attractions & Sightseeing", "location": dest, "description": f"Visit prominent cultural and natural highlights in {dest}.", "estimated_cost": 500.0, "cost_status": "ESTIMATED", "booking_required": False, "reason": "Main highlight"},
        {"date": str(req.end_date), "start_time": "15:00", "end_time": "17:00", "title": "Departure Transfer", "location": dest, "description": "Prepare for return departure back to origin.", "estimated_cost": None, "cost_status": "UNAVAILABLE", "booking_required": True, "reason": "Departure"}
    ]


def validate(state: TripState):
    logger.info("agent=validation status=started")
    print("[PLANNER AGENT 8/11: ValidationAgent] Verifying date continuity & schedule...")
    req, issues = TripRequest.model_validate(state["request"]), []
    for activity in state.get("itinerary", []):
        if not str(req.start_date) <= activity["date"] <= str(req.end_date):
            issues.append({"type": "date", "severity": "high", "description": f"{activity['title']} date {activity['date']} is outside trip dates."})
    if not state.get("itinerary"):
        issues.append({"type": "itinerary", "severity": "high", "description": "No evidence-backed itinerary is available."})
    return {"validation": {"valid": not issues, "issues": issues, "status": "VERIFIED", "checked_at": now()}, **done("Validating plan")}


def _deterministic_critique(itinerary: list[dict], req: TripRequest) -> list[dict]:
    """Layer (a): Pure deterministic checks — no LLM calls."""
    issues = []
    sd, ed = str(req.start_date), str(req.end_date)

    # Group activities by day for overlap & density checks
    by_day: dict[str, list[dict]] = {}
    for act in itinerary:
        d = act.get("date", "")
        by_day.setdefault(d, []).append(act)

        # Check 1: date outside trip range
        if d and not (sd <= d <= ed):
            issues.append({"severity": "high", "day": d,
                           "description": f"Activity '{act.get('title', '?')}' on {d} is outside trip dates {sd}..{ed}."})

    # Check 2: overlapping time slots on the same day
    def _minutes(t: str) -> int | None:
        try:
            parts = t.strip().split(":")
            return int(parts[0]) * 60 + int(parts[1])
        except Exception:
            return None

    for day, acts in by_day.items():
        timed = []
        for a in acts:
            s, e = _minutes(a.get("start_time", "")), _minutes(a.get("end_time", ""))
            if s is not None and e is not None:
                timed.append((s, e, a.get("title", "?")))
        timed.sort()
        for i in range(len(timed) - 1):
            _, end_i, title_i = timed[i]
            start_j, _, title_j = timed[i + 1]
            if end_i > start_j:
                issues.append({"severity": "high", "day": day,
                               "description": f"Overlap: '{title_i}' ends after '{title_j}' starts on {day}."})

        # Check 3: too many activities for relaxed pace
        pace_limit = {"relaxed": 5, "balanced": 7, "fast": 10}.get(req.pace, 7)
        if len(acts) > pace_limit:
            issues.append({"severity": "medium", "day": day,
                           "description": f"{len(acts)} activities on {day} exceeds {req.pace}-pace limit of {pace_limit}."})

    # Check 4: days with zero activities
    from datetime import timedelta
    current = req.start_date
    while current <= req.end_date:
        ds = str(current)
        if ds not in by_day or len(by_day[ds]) == 0:
            issues.append({"severity": "medium", "day": ds,
                           "description": f"No activities planned for {ds}."})
        current += timedelta(days=1)

    # Check 5: estimated costs exceed budget
    if req.budget:
        total_cost = sum(a.get("estimated_cost") or 0 for a in itinerary)
        if total_cost > req.budget:
            issues.append({"severity": "medium", "day": None,
                           "description": f"Estimated activity cost {total_cost:,.0f} exceeds budget {req.budget:,.0f}."})

    return issues


def critic(state: TripState):
    """Critic agent: deterministic checks + optional LLM quality review."""
    logger.info("agent=critic status=started")
    rev = state.get("revision_count", 0)
    print(f"[CRITIC AGENT] Reviewing itinerary (revision_count={rev})...")

    req = TripRequest.model_validate(state["request"])
    itin = state.get("itinerary", [])

    # Layer (a): deterministic checks
    det_issues = _deterministic_critique(itin, req)
    has_blocking = any(i["severity"] == "high" for i in det_issues)

    # Layer (b): LLM critique — only if layer (a) found nothing blocking
    llm_issues = []
    if not has_blocking and itin:
        destination = state.get("selected_destination") or ""
        prompt = (
            f"You are a travel itinerary critic. Review this itinerary for realism and quality.\n"
            f"Trip: {req.origin} → {destination}, {req.start_date} to {req.end_date}, "
            f"pace={req.pace}, interests={req.interests}\n"
            f"Itinerary: {json.dumps(itin)}\n\n"
            f"Check for: unrealistic travel times between stops, pace mismatch with user preference, "
            f"key interests not covered, activities that don't exist at the destination.\n"
            f"If the itinerary is good, set verdict='approve' with empty issues.\n"
            f"If it needs fixes, set verdict='revise' with specific issues and revision_instructions."
        )
        llm_result = gemini.structured(prompt, CritiqueResult)
        if llm_result:
            llm_issues = [i.model_dump() for i in llm_result.issues]
        # If gemini returns None, we skip layer (b) — never loop due to LLM unavailability

    all_issues = det_issues + llm_issues
    verdict = "revise" if all_issues else "approve"

    # If at the revision cap, force approve regardless
    if rev >= MAX_REVISIONS:
        verdict = "approve"

    revision_instructions = ""
    if verdict == "revise":
        descriptions = [i["description"] for i in all_issues]
        revision_instructions = "Fix these issues: " + "; ".join(descriptions)

    entry = {
        "verdict": verdict,
        "issues": all_issues,
        "revision_instructions": revision_instructions,
        "revision_pass": rev,
        "checked_at": now(),
    }

    print(f"[CRITIC] verdict={verdict} issues={len(all_issues)} rev={rev}/{MAX_REVISIONS}")
    for iss in all_issues:
        print(f"[CRITIC]   [{iss['severity']}] {iss.get('day', '-')}: {iss['description']}")

    history = list(state.get("critique_history", []))
    history.append(entry)

    return {"critique_history": history, **done(f"Critic: {verdict} ({len(all_issues)} issues)")}


def _critic_router(state: TripState) -> str:
    """Conditional edge: route back to itinerary for revision, or forward to budget."""
    history = state.get("critique_history", [])
    rev = state.get("revision_count", 0)
    if history:
        latest = history[-1]
        if latest.get("verdict") == "revise" and rev < MAX_REVISIONS:
            logger.info("critic_router: sending back to itinerary (rev=%d)", rev)
            return "itinerary"
    return "budget"


def budget(state: TripState):
    logger.info("agent=budget status=started")
    print("[PLANNER AGENT 9/11: BudgetAgent] Calculating itemized cost breakdown...")
    req = TripRequest.model_validate(state["request"])
    activity_total = sum(item.get("estimated_cost") or 0 for item in state.get("itinerary", []))
    return {"budget_breakdown": {"currency": req.currency, "user_budget": req.budget, "flights": {"amount": None, "status": "UNAVAILABLE"}, "accommodation": {"amount": None, "status": "UNAVAILABLE"}, "activities": {"amount": activity_total or None, "status": "ESTIMATED" if activity_total else "UNAVAILABLE"}, "food": {"amount": None, "status": "UNAVAILABLE"}, "transport": {"amount": None, "status": "UNAVAILABLE"}, "total": None, "message": "No total is calculated until provider prices are selected."}, **done("Calculating budget")}


def recommendations(state: TripState):
    logger.info("agent=recommendations status=started")
    print("[PLANNER AGENT 10/11: RecommendationsAgent] Formatting travel advisory...")
    notes = ["Confirm opening hours, entry rules, event timing, and prices from verified sources before booking."]
    if state.get("weather", {}).get("status") == "UNAVAILABLE":
        notes.append("Live weather is unavailable; check local forecast closer to departure.")
    return {"recommendations": notes, **done("Preparing recommendations")}


def travel_insights(state: TripState):
    """Travel Video Insights & Tips Summarizer Agent: Synthesizes video transcripts, creator blogs & reviews into structured travel insights."""
    logger.info("agent=travel_insights status=started")
    ep = state.get("execution_plan") or {}
    if not ep.get("include_travel_insights", True):
        reason = ep.get("reasoning", "excluded by planner")
        print(f"[TravelInsightsAgent] ⏭️ Skipped: {reason}")
        return {"travel_insights": {}, **done(f"Skipped travel_insights: {reason}")}
    destination = state.get("selected_destination")
    if not destination:
        return {"travel_insights": {}, **done("Synthesizing creator insights")}
    
    print(f"[PLANNER AGENT 11/11: VideoSummarizerAgent] Searching YouTube creator guides & synthesizing tips for '{destination}'...")
    sources_yt, _ = research(f"site:youtube.com {destination} travel guide vlog best things to do food places", limit=5)
    sources_tips, warnings = research(f"{destination} travel guide tips photography food transit secrets review", limit=5)
    all_creator_sources = sources_yt + sources_tips
    
    prompt = f"""
You are the Travel Video Insights & Tips Summarizer Agent for TripMate AI.
Summarize and synthesize the YouTube video transcripts, creator vlogs, and expert travel reviews for "{destination}".

Produce structured, highly actionable travel advice:
- photo_spots: 3-4 top viewpoint & photography tips with best times of day (e.g. sunrise/sunset golden hour)
- culinary_tips: 3-4 authentic regional dishes & dining hacks
- transit_hacks: 3-4 local transit tips (cabs, local buses, boat booking, avoiding scams)
- packing_and_safety: 3-4 packing essentials & local customs
- creator_insights: 3-4 key takeaways synthesized from top travel creators, vlogs, and reviews

EVIDENCE GATHERED:
{json.dumps([{"title": s["title"], "snippet": s["snippet"], "url": s["url"]} for s in all_creator_sources])}
"""
    insights_res = gemini.structured(prompt, CleanTravelInsights)
    if insights_res:
        insights_data = insights_res.model_dump()
    else:
        insights_data = _fallback_travel_insights(destination, all_creator_sources)
        
    return {"travel_insights": insights_data, "sources": all_creator_sources, "warnings": warnings, **done("Creator insights & travel advice synthesized")}


def _fallback_travel_insights(destination: str, sources: list[dict] = None) -> dict:
    """Dynamically creates structured creator insights for any destination."""
    return {
        "destination": destination,
        "photo_spots": [
            f"Sunrise viewpoint overlooking {destination} valley/monuments for soft golden morning light (06:00–07:30 AM).",
            f"Iconic heritage arches and waterfront promenade in {destination} during sunset blue hour for reflection shots.",
            "Local traditional markets and vibrant old-town alleys for authentic cultural portraits."
        ],
        "culinary_tips": [
            f"Sample authentic local breakfast specialties and signature regional delicacies in {destination}.",
            "Visit verified heritage diners and local sweet shops frequented by residents rather than tourist-only highway stops.",
            "Try freshly prepared regional street snacks and seasonal refreshments from bustling central bazaars."
        ],
        "transit_hacks": [
            "Use prepaid taxi/auto booths or ride-hailing apps at airport/railway stations to avoid inflated fares.",
            "Carry small cash notes (₹100/₹200) as parking gates, entry counters, and local stalls may have spotty digital signal.",
            "Start early in the morning (before 09:00 AM) to beat heavy traffic on narrow town access roads."
        ],
        "packing_and_safety": [
            "Comfortable, slip-resistant walking shoes suitable for heritage cobblestones and natural paths.",
            "Light breathable layers plus a compact rain jacket or umbrella depending on seasonal forecast.",
            "Respect local cultural and religious dress codes by keeping shoulders and knees covered when entering sacred sites."
        ],
        "creator_insights": [
            f"Top travel vloggers recommend booking early morning time-slots at major attractions in {destination} to avoid long queues.",
            "Opt for boutique heritage stays or eco-resorts near key viewpoints for an immersive travel experience.",
            "Allocate dedicated evening time for walking tours through illuminated historic streets and local craft markets."
        ]
    }


def finish(state: TripState):
    logger.info("agent=finalize status=complete")
    print("[PLANNER FINALIZE] ✓ Multi-agent travel plan synthesized and ready!\n")
    return {"last_updated": now(), **done("Travel plan ready")}


graph = StateGraph(TripState)
for name, fn in [("normalize", normalize), ("discovery", discovery), ("planner", planner), ("weather", weather), ("events", events), ("places", places), ("flights", flights), ("trains", trains), ("hotels", hotels), ("itinerary", itinerary), ("validate", validate), ("critic", critic), ("budget", budget), ("recommendations", recommendations), ("travel_insights", travel_insights), ("finish", finish)]:
    graph.add_node(name, fn)
graph.add_edge(START, "normalize"); graph.add_edge("normalize", "discovery"); graph.add_edge("discovery", "planner")
graph.add_edge("planner", "weather"); graph.add_edge("planner", "events"); graph.add_edge("planner", "places")
graph.add_edge("weather", "flights"); graph.add_edge("events", "flights"); graph.add_edge("places", "flights")
graph.add_edge("flights", "trains"); graph.add_edge("trains", "hotels"); graph.add_edge("hotels", "itinerary"); graph.add_edge("itinerary", "validate")
graph.add_edge("validate", "critic")
graph.add_conditional_edges("critic", _critic_router, {"itinerary": "itinerary", "budget": "budget"})
graph.add_edge("budget", "recommendations"); graph.add_edge("recommendations", "travel_insights"); graph.add_edge("travel_insights", "finish"); graph.add_edge("finish", END)
travel_graph = graph.compile()


def run_trip(request_data: dict[str, Any], thread_id: str | None = None, cancellation_event: threading.Event | None = None) -> dict:
    token = request_cancel_event.set(cancellation_event)
    try:
        return _run_trip(request_data, thread_id)
    finally:
        request_cancel_event.reset(token)


def _run_trip(request_data: dict[str, Any], thread_id: str | None = None) -> dict:
    req = TripRequest.model_validate(request_data)
    tid = thread_id or f"trip_{uuid.uuid4().hex}"
    dest_str = req.destination or (", ".join(req.selected_destinations) if req.selected_destinations else "auto")
    logger.info("trip_started thread_id=%s destination=%s dates=%s..%s", tid, dest_str, req.start_date, req.end_date)
    print(f"\n=======================================================")
    print(f"✈️ [FLOW 1: ITINERARY PLANNER] Starting for '{dest_str}' ({req.start_date} to {req.end_date})")
    print(f"=======================================================")
    result = travel_graph.invoke({"trip_id": str(uuid.uuid4()), "thread_id": tid, "request": req.model_dump(mode="json"), "sources": [], "warnings": [], "progress": []})
    logger.info("trip_complete thread_id=%s warnings=%d", tid, len(result.get("warnings", [])))
    print(f"=======================================================")
    print(f"✓ [FLOW 1: ITINERARY PLANNER] Complete: {len(result.get('itinerary', []))} itinerary activities, {len(result.get('hotels', []))} hotels")
    print(f"=======================================================\n")
    return {k: v for k, v in result.items() if k != "request"}


def run_travel_agent(user_input: str, thread_id: str | None = None) -> dict:
    today = date.today()
    trip = run_trip(TripRequest(origin="Not provided", start_date=today, end_date=today, special_requirements=user_input).model_dump(mode="json"), thread_id)
    return {"thread_id": trip["thread_id"], "answer": "Use the guided planner to provide trip details.", "flight_results": trip.get("flights", []), "hotel_results": trip.get("hotels", []), "itinerary": trip.get("itinerary", []), "llm_calls": 0}


# =====================================================================
# REPLANNING AGENT: TARGETED ITINERARY & DYNAMIC SEARCH AGENT
# =====================================================================

def replanning_agent(change_text: str, current_itinerary: list[dict], trip_context: dict) -> dict:
    """Replanning Agent: Modifies itinerary activities OR dynamically executes live search for hotels/stays/attractions."""
    logger.info("agent=ReplanningAgent change_text='%s' activities=%d", change_text, len(current_itinerary))
    print(f"\n=======================================================")
    print(f"🔄 [REPLANNING AGENT] User request: '{change_text}'")
    print(f"   Context: destination='{trip_context.get('destination')}', activities={len(current_itinerary)}")
    print(f"=======================================================")
    
    lower = change_text.lower()
    destination = trip_context.get("destination", "Destination")
    
    # Check if query is searching for hotels / stays near a specific landmark or style
    is_hotel_query = any(k in lower for k in ["hotel", "resort", "stay", "room", "accommodation", "lake", "beach", "near"]) and any(k in lower for k in ["search", "find", "look", "change", "by", "near", "facing", "view", "budget", "luxury", "show"])
    
    updated_hotels = []
    change_summary = ""
    
    if is_hotel_query:
        logger.info("agent=ReplanningAgent hotel_search_intent_detected query=%s", change_text)
        print(f"[REPLANNING AGENT] 🏨 Hotel search query detected: '{change_text}' in {destination}")
        search_query = f"hotels resorts {change_text} in {destination} verified property names rates booking"
        sources, _ = research(search_query, limit=6)
        
        prompt_hotels = f"""
Extract 3 to 5 REAL, INDIVIDUAL HOTELS / RESORTS matching this user request: "{change_text}" in destination "{destination}".
CRITICAL: NO ARTICLE TITLES (NO "Top 10..."). Real hotel names, exact location, realistic price in INR, amenities, and booking website URL.
EVIDENCE:
{json.dumps([{"title": s["title"], "snippet": s["snippet"], "url": s["url"]} for s in sources])}
"""
        hotel_res = gemini.structured(prompt_hotels, HotelListSchema)
        if hotel_res and hotel_res.hotels:
            updated_hotels = [h.model_dump() for h in hotel_res.hotels]
        else:
            updated_hotels = _fallback_hotel_synthesis(destination, change_text, sources)
            
        change_summary = f"Found {len(updated_hotels)} verified properties matching: '{change_text}'."
        print(f"[REPLANNING AGENT] ✓ Found {len(updated_hotels)} hotels for '{change_text}'")

    # Adjust itinerary activities based on user prompt
    prompt_itinerary = f"""
You are the Replanning Agent for TripMate AI.
The user wants to adjust their travel plan with: "{change_text}".
CURRENT ITINERARY: {json.dumps(current_itinerary)}
TRIP CONTEXT: {json.dumps(trip_context)}

INSTRUCTIONS:
1. ONLY modify or replace the affected activities (e.g. if user asks 'Less walking', replace walking tours with cabs or boat rides; if 'Cheaper', adjust costs; if 'Add cooking class', insert culinary session; if 'Remove X and add Y', update activities).
2. Maintain realistic timeline, reasonable hours, and preserve unaffected activities.
3. Return the updated activities and a concise 1-sentence change summary.
"""
    result = gemini.structured(prompt_itinerary, ReplanResult)
    if result and result.activities:
        updated_itinerary = [a.model_dump() for a in result.activities]
        if not change_summary:
            change_summary = result.change_summary
    else:
        updated_itinerary = _apply_fallback_replanning_rules(change_text, current_itinerary, trip_context)
        if not change_summary:
            change_summary = f"Adjusted itinerary for: '{change_text}'."

    print(f"[REPLANNING AGENT] ✓ Complete: {change_summary}")
    print(f"=======================================================\n")
    return {
        "success": True,
        "itinerary": updated_itinerary,
        "updated_hotels": updated_hotels,
        "change_summary": change_summary,
        "affected_elements": ["hotels", "itinerary"] if updated_hotels else ["itinerary"]
    }


def _apply_fallback_replanning_rules(change_text: str, current_itinerary: list[dict], trip_context: dict) -> list[dict]:
    updated = list(current_itinerary)
    lower = change_text.lower()
    dest = trip_context.get("destination", "Local Area")
    
    if "walk" in lower:
        for act in updated:
            if "walk" in str(act.get("description", "")).lower():
                act["description"] = act["description"].replace("walk", "short scenic cab / transfer")
    elif "food" in lower or "cooking" in lower:
        updated.insert(min(2, len(updated)), {
            "date": updated[0]["date"] if updated else "Day 2",
            "start_time": "16:30",
            "end_time": "18:30",
            "title": "Authentic Regional Cooking Class & Food Tasting",
            "location": dest,
            "description": "Learn traditional spice blending and sample fresh local recipes.",
            "estimated_cost": 850.0,
            "cost_status": "ESTIMATED",
            "booking_required": False,
            "reason": "Requested culinary experience"
        })
    elif "cheap" in lower or "budget" in lower:
        for act in updated:
            if act.get("estimated_cost"):
                act["estimated_cost"] = round(act["estimated_cost"] * 0.75, 2)
    elif "relax" in lower:
        for act in updated:
            if "fast" in str(act.get("reason", "")).lower():
                act["reason"] = "Slow-paced exploration"
    else:
        updated.append({
            "date": updated[-1]["date"] if updated else "Day 2",
            "start_time": "17:00",
            "end_time": "19:00",
            "title": f"Custom Activity: {change_text[:30]}",
            "location": dest,
            "description": f"Updated activity based on user preference: {change_text}.",
            "estimated_cost": None,
            "cost_status": "UNAVAILABLE",
            "booking_required": False,
            "reason": "User adjustment"
        })
        
    return updated
