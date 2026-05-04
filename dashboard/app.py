from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
import json

app = FastAPI(title="TrumpTruths Market Analyzer")
ROOT = Path(__file__).parent.parent
ANALYSES_FILE = ROOT / "post_analyses.json"
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


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "dashboard.html")
