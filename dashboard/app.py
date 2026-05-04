from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
import json
import logging

from dotenv import load_dotenv

from . import behavior

# Load env so Claude calls from /api/behavior pick up ANTHROPIC_API_KEY.
ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="TrumpTruths Market Analyzer")
ANALYSES_FILE = ROOT / "post_analyses.json"
BEHAVIOR_CACHE = ROOT / "behavior_cache.json"
STATIC_DIR = Path(__file__).parent / "static"


def _load_posts() -> list[dict]:
    if not ANALYSES_FILE.exists():
        return []
    try:
        with ANALYSES_FILE.open(encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


@app.get("/api/posts")
def get_posts():
    posts = _load_posts()
    return sorted(posts, key=lambda p: p.get("analyzed_at", ""), reverse=True)


@app.get("/api/stats")
def get_stats():
    posts = _load_posts()
    if not posts:
        return {"total": 0, "with_impact": 0, "by_severity": {}, "last_analyzed_at": None}

    with_impact = sum(1 for p in posts if p.get("analysis", {}).get("has_market_impact"))
    by_severity: dict[str, int] = {}
    for p in posts:
        sev = p.get("analysis", {}).get("severity", "none")
        by_severity[sev] = by_severity.get(sev, 0) + 1
    last = max((p.get("analyzed_at", "") for p in posts), default=None)

    return {
        "total": len(posts),
        "with_impact": with_impact,
        "by_severity": by_severity,
        "last_analyzed_at": last,
    }


@app.get("/api/behavior")
def get_behavior(
    force: bool = Query(False, description="Force regeneration, bypassing cache"),
    cache_only: bool = Query(False, description="Return cache if present, never generate"),
):
    """Return the latest behavioral forecast.

    Generation is an explicit user action — the endpoint never silently spends
    Claude credits. Behavior:
    - cache exists, no `force`        -> return cache (with `is_stale` flag if TTL exceeded)
    - cache missing, `cache_only=1`   -> 204 No Content (UI shows empty state)
    - cache missing, no force         -> generate ONCE on first hit, cache, return
    - `force=1`                       -> regenerate, overwrite cache, return
    """
    from fastapi.responses import Response

    cached = behavior.load_cached(BEHAVIOR_CACHE)

    if cached and not force:
        return {**cached, "from_cache": True, "is_stale": not behavior.is_fresh(cached)}

    if cache_only:
        return Response(status_code=204)

    try:
        payload = behavior.generate_behavior(ANALYSES_FILE)
    except Exception as e:
        logger.exception("behavior generation failed")
        if cached:
            return {**cached, "from_cache": True, "is_stale": True,
                    "warning": f"regen failed: {e}; serving stale"}
        raise HTTPException(status_code=502, detail=f"Behavior generation failed: {e}")

    behavior.save_cache(BEHAVIOR_CACHE, payload)
    return {**payload, "from_cache": False, "is_stale": False}


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "dashboard.html")
