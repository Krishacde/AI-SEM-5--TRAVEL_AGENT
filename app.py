import sys
import asyncio
import threading
import time
import os
from pathlib import Path

# Reconfigure stdout/stderr for Windows UTF-8 console output
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

import logging
import traceback
import uvicorn

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from backend import RequestCancelled, TripRequest, run_travel_agent, run_trip, run_discovery, replanning_agent
from tools.flight_tool import API_KEY as AVIATIONSTACK_API_KEY

BASE_DIR = Path(__file__).resolve().parent

# Edit these two values to tune the request limiter for local or hosted use.
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_MAX_REQUESTS = 6
_rate_limit_history: dict[str, list[float]] = {}
_rate_limit_lock = threading.Lock()
logger = logging.getLogger("tripmate.api")
logger.setLevel(logging.INFO)
log_formatter = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")

if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler) for h in logger.handlers):
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(log_formatter)
    logger.addHandler(console_handler)

if not any(isinstance(handler, logging.FileHandler) for handler in logger.handlers):
    file_handler = logging.FileHandler(BASE_DIR / "tripmate.log", encoding="utf-8")
    file_handler.setFormatter(log_formatter)
    logger.addHandler(file_handler)

app = FastAPI(
    title="TripMate AI",
    description="LangGraph Multi-Agent Travel Planner with FastAPI Frontend",
    version="2.0.0"
)

app.mount(
    "/static",
    StaticFiles(directory=str(BASE_DIR / "static")),
    name="static"
)

templates = Jinja2Templates(
    directory=str(BASE_DIR / "templates")
)


def rate_limit_response(request: Request, bucket: str) -> JSONResponse | None:
    client_key = f"{bucket}:{request.client.host if request.client else 'unknown'}"
    now = time.monotonic()
    with _rate_limit_lock:
        recent = [stamp for stamp in _rate_limit_history.get(client_key, []) if now - stamp < RATE_LIMIT_WINDOW_SECONDS]
        if len(recent) >= RATE_LIMIT_MAX_REQUESTS:
            _rate_limit_history[client_key] = recent
            return JSONResponse(
                status_code=429,
                content={"success": False, "error": "Too many requests. Please wait before starting another agent run."},
                headers={"Retry-After": str(RATE_LIMIT_WINDOW_SECONDS)},
            )
        recent.append(now)
        _rate_limit_history[client_key] = recent
    return None


async def run_cancellable(request: Request, operation, *args, **kwargs):
    cancellation_event = threading.Event()
    task = asyncio.create_task(asyncio.to_thread(operation, *args, cancellation_event=cancellation_event, **kwargs))
    while not task.done():
        await asyncio.wait({task}, timeout=0.25)
        if await request.is_disconnected():
            cancellation_event.set()
            break
    try:
        return await task
    except RequestCancelled:
        raise


class TravelRequest(BaseModel):
    message: str
    thread_id: str | None = None


class GuidedTripRequest(TripRequest):
    thread_id: str | None = None


class ProviderSelectionRequest(BaseModel):
    trip_id: str
    provider_type: str
    option: dict


class DiscoveryQueryRequest(BaseModel):
    origin: str
    month: str = "August"
    year: int = 2026
    days: int = Field(default=5, ge=1, le=30)
    start_date: str | None = None
    end_date: str | None = None
    travelers: int = 1
    budget: float | None = None
    currency: str = "INR"
    mood: str | None = None
    interests: list[str] = []
    weather_preference: str | None = "Pleasant"
    pace: str | None = "Balanced"
    accommodation: str | None = "Mid-range hotel"
    special_requirements: str | None = None


class ReplanPayload(BaseModel):
    trip_id: str
    change_text: str
    itinerary: list[dict] = []
    trip_context: dict = {}


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={}
    )


@app.get("/discover", response_class=HTMLResponse)
async def discover_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="discover.html",
        context={}
    )


@app.post("/api/travel")
async def travel_planner(request: Request, request_data: TravelRequest):
    limited = rate_limit_response(request, "travel")
    if limited:
        return limited
    try:
        user_message = request_data.message.strip()
        if not user_message:
            return JSONResponse(
                status_code=400,
                content={"success": False, "error": "Message cannot be empty."}
            )

        result = run_travel_agent(
            user_input=user_message,
            thread_id=request_data.thread_id
        )

        return JSONResponse(
            content={
                "success": True,
                "thread_id": result["thread_id"],
                "answer": result["answer"],
                "flight_results": result["flight_results"],
                "hotel_results": result["hotel_results"],
                "itinerary": result["itinerary"],
                "llm_calls": result["llm_calls"],
            }
        )

    except Exception as e:
        logger.exception("travel_planner failed: %s", e)
        traceback.print_exc()
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e)}
        )


@app.post("/api/trips/{trip_id}/selections")
async def select_provider(trip_id: str, request_data: ProviderSelectionRequest):
    """Return a deterministic demo update for a selected flight or hotel."""
    if trip_id != request_data.trip_id:
        return JSONResponse(status_code=400, content={"success": False, "error": "Trip id does not match selection."})
    if request_data.provider_type not in {"flight", "hotel", "cab"}:
        return JSONResponse(status_code=400, content={"success": False, "error": "Unsupported provider selection."})

    option_name = request_data.option.get("name") or request_data.option.get("airline") or "Selected option"
    detail = request_data.option.get("flight_number") or request_data.option.get("area") or request_data.option.get("location") or "provider option"
    label = {"flight": "flight", "hotel": "hotel", "cab": "transport"}[request_data.provider_type]
    return {
        "success": True,
        "trip_id": trip_id,
        "selection": {"type": request_data.provider_type, "name": option_name, "option": request_data.option},
        "itinerary_update": {
            "time": "Plan update",
            "title": f"Selected {label}: {option_name}",
            "description": f"{detail} added to the plan. Connected provider APIs can replace this estimate."
        },
        "message": f"{option_name} added. Your itinerary was refreshed."
    }


@app.post("/api/trips")
async def create_trip(request: Request, request_data: GuidedTripRequest):
    """Run the evidence-first LangGraph planner with structured user choices."""
    limited = rate_limit_response(request, "trip")
    if limited:
        return limited
    try:
        payload = request_data.model_dump(mode="json", exclude={"thread_id"})
        logger.info("request=trip_create status=started thread_id=%s destination=%s", request_data.thread_id or "new", payload.get("destination"))
        trip = await run_cancellable(request, run_trip, payload, request_data.thread_id)
        return JSONResponse(content={"success": True, "trip": trip})
    except RequestCancelled:
        return JSONResponse(status_code=499, content={"success": False, "cancelled": True, "error": "Trip research cancelled."})
    except Exception as exc:
        logger.exception("request=trip_create status=failed error=%s", exc)
        traceback.print_exc()
        return JSONResponse(status_code=400, content={"success": False, "error": str(exc)})


@app.post("/api/discover")
async def discover_destinations(request: Request, request_data: DiscoveryQueryRequest):
    """Run LangGraph Multi-Agent Discovery Pipeline to find clean, ranked destination cards."""
    limited = rate_limit_response(request, "discover")
    if limited:
        return limited
    try:
        logger.info(
            "request=destination_discovery status=started origin=%s month=%s year=%s",
            request_data.origin, request_data.month, request_data.year
        )
        discovery_result = await run_cancellable(request, run_discovery,
            origin=request_data.origin.strip(),
            month=request_data.month.strip(),
            year=request_data.year,
            days=request_data.days,
            interests=request_data.interests,
            mood=request_data.mood
        )
        return JSONResponse(content={
            "success": True,
            "destinations": discovery_result["destinations"],
            "scout_summary": discovery_result["scout_summary"],
            "progress": discovery_result["progress"],
            "warnings": discovery_result["warnings"]
        })
    except RequestCancelled:
        return JSONResponse(status_code=499, content={"success": False, "cancelled": True, "error": "Destination research cancelled."})
    except Exception as exc:
        logger.exception("request=destination_discovery status=failed error=%s", exc)
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"success": False, "error": str(exc)})


@app.post("/api/trips/replan")
async def replan_itinerary(request: Request, request_data: ReplanPayload):
    """Run ReplanningAgent to adjust itinerary based on user input without full regeneration."""
    limited = rate_limit_response(request, "replan")
    if limited:
        return limited
    try:
        logger.info("request=trip_replan status=started trip_id=%s change=%s", request_data.trip_id, request_data.change_text)
        result = replanning_agent(
            change_text=request_data.change_text,
            current_itinerary=request_data.itinerary,
            trip_context=request_data.trip_context
        )
        return JSONResponse(content={"success": True, **result})
    except Exception as exc:
        logger.exception("request=trip_replan status=failed error=%s", exc)
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"success": False, "error": str(exc)})


@app.get("/api/diagnostics")
async def diagnostics():
    """Report integration readiness without exposing secret values."""
    def key_state(name: str) -> str:
        value = os.getenv(name, "").strip()
        if not value:
            return "missing"
        if value.lower().startswith("your_") or value.lower() in {"change_me", "placeholder"}:
            return "placeholder"
        return "configured"

    keys = {
        "GEMINI_API_KEY": key_state("GEMINI_API_KEY"),
        "TAVILY_API_KEY": key_state("TAVILY_API_KEY"),
        "AVIATIONSTACK_API_KEY": key_state("AVIATIONSTACK_API_KEY"),
        "DATABASE_URL": key_state("DATABASE_URL"),
    }
    logger.info("request=diagnostics keys=%s", keys)
    return {
        "status": "ok",
        "keys": {name: "configured" if value else "missing" for name, value in keys.items()},
        "notes": {
            "AVIATIONSTACK_API_KEY": "Live flight status lookup.",
            "GEMINI_API_KEY": "Required for multi-agent reasoning, ranking, planning and replanning.",
            "TAVILY_API_KEY": "Required for web research, verified events, and hotel evidence."
        }
    }


@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "message": "TripMate AI Multi-Agent Planner is running"
    }


@app.get("/favicon.ico")
async def favicon():
    return JSONResponse(content={})


if __name__ == "__main__":
    uvicorn.run(
        "app:app",
        host="127.0.0.1",
        port=8000,
        reload=False
    )
