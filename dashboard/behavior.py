"""Behavioral forecast generator.

Reads recent original posts from `post_analyses.json`, sends them to Claude
with a behavioral-analysis prompt, and returns structured predictions.
Caches the result to disk so refreshes don't burn credits.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from anthropic import Anthropic

logger = logging.getLogger(__name__)

CLAUDE_MODEL = "claude-sonnet-4-6"

# How many hours of post history to feed into the analysis.
INPUT_WINDOW_HOURS = 48

# Max number of posts to send (newest-first) to bound prompt size + cost.
MAX_INPUT_POSTS = 50

# Cache: regenerate only if older than this OR if the user forces it.
CACHE_TTL_MINUTES = 30


SYSTEM_PROMPT = """You are a behavioral analyst studying public posting patterns of a political figure on social media. You will receive their last several first-person original posts in chronological order with timestamps and severity ratings from a market-impact analyzer. Your job is to identify behavioral patterns, predict likely near-term posting and announcement behavior, and flag external triggers that would shift their behavior.

Be analytical and grounded. Cite specific posts as evidence. Don't psychoanalyze inner mental states beyond what's directly observable in posting patterns. Frame predictions as "based on the recent rhetoric and observed pattern X, likely to..." rather than speculation.

Respond with ONLY a valid JSON object — no markdown fences, no preamble — matching this exact schema:

{
  "current_mood": "<short descriptor of current posting tone, e.g. 'agitated, foreign-policy focused' / 'celebratory' / 'scattered, multi-topic' / 'cooling'>",
  "summary": "<2-3 sentence overall behavioral read of the recent window>",
  "active_themes": [
    {
      "theme": "<short slug like 'iran-hormuz' or 'china-tariffs'>",
      "label": "<human-readable, e.g. 'Iran / Strait of Hormuz'>",
      "intensity": "low" | "medium" | "high",
      "trajectory": "rising" | "stable" | "cooling",
      "post_count": <int>,
      "first_seen_hours_ago": <number>,
      "evidence": "<1-2 sentences citing specific recent posts by content or topic>"
    }
  ],
  "predictions": [
    {
      "horizon": "6h" | "24h" | "72h",
      "prediction": "<concrete posting or announcement behavior to expect>",
      "confidence": "low" | "medium" | "high",
      "rationale": "<grounded in observed pattern>",
      "what_to_watch_for": "<specific phrases, named entities, time markers, or behaviors that would confirm the prediction>"
    }
  ],
  "trigger_watchlist": [
    {
      "trigger": "<external event likely to provoke a post or response>",
      "expected_response": "<what the response is likely to look like — tone, severity, topic>",
      "response_window_hours": <number>
    }
  ]
}

Rules:
- Produce 2-5 active_themes. Skip themes with only 1 post unless they're highly distinctive.
- Produce 3-5 predictions covering varied time horizons.
- Produce 2-4 trigger_watchlist items focused on plausible external events in the next 24-72 hours.
- All numeric fields must be numbers (not strings).
- "first_seen_hours_ago" is roughly when the theme first appeared in this window.
- If the input is too thin to support an analysis, return empty arrays and current_mood="insufficient data".
"""


def _load_posts(analyses_file: Path) -> list[dict]:
    if not analyses_file.exists():
        return []
    try:
        with analyses_file.open(encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"behavior: failed to load analyses: {e}")
        return []


def _flatten_recent_originals(posts: list[dict]) -> list[dict]:
    """Walk parent records + their related_posts, return originals only,
    flattened and sorted by post timestamp ascending. Filters to the input
    window."""
    out: list[dict] = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=INPUT_WINDOW_HOURS)

    def _push(record: dict, analysis: dict | None):
        if (analysis or {}).get("post_kind") == "link_share":
            return
        ts_raw = record.get("timestamp") or record.get("analyzed_at")
        if not ts_raw:
            return
        try:
            ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return
        if ts < cutoff:
            return
        out.append({
            "ts": ts,
            "post_id": record.get("post_id"),
            "content": record.get("content", ""),
            "severity": (analysis or {}).get("severity", "none"),
            "topic_signature": (analysis or {}).get("topic_signature", ""),
        })

    for p in posts:
        _push(p, p.get("analysis"))
        for r in p.get("related_posts", []) or []:
            _push(r, r.get("analysis"))

    out.sort(key=lambda x: x["ts"])
    if len(out) > MAX_INPUT_POSTS:
        # Keep the most recent N
        out = out[-MAX_INPUT_POSTS:]
    return out


def _build_user_message(recent: list[dict]) -> str:
    if not recent:
        return "No recent original posts in the input window."

    lines = [
        f"Window: last {INPUT_WINDOW_HOURS}h.",
        f"Total original posts in window: {len(recent)}.",
        "",
        "Posts (oldest first):",
    ]
    now = datetime.now(timezone.utc)
    for p in recent:
        age_h = (now - p["ts"]).total_seconds() / 3600
        topic = f" [topic: {p['topic_signature']}]" if p["topic_signature"] and p["topic_signature"] != "none" else ""
        # Trim very long posts to keep token cost bounded
        body = p["content"][:600] + ("…" if len(p["content"]) > 600 else "")
        lines.append(
            f"- #{p['post_id']} ({age_h:.1f}h ago, severity={p['severity']}{topic}):\n  {body}"
        )
    return "\n".join(lines)


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        # Remove the first fence line and the trailing ```
        text = text.split("\n", 1)[1]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    return text.strip()


def generate_behavior(analyses_file: Path) -> dict:
    """Generate a fresh behavioral analysis. Raises on Claude error."""
    client = Anthropic()  # reads ANTHROPIC_API_KEY from env

    posts = _load_posts(analyses_file)
    recent = _flatten_recent_originals(posts)
    user_message = _build_user_message(recent)

    logger.info(
        f"behavior: generating analysis from {len(recent)} posts in last {INPUT_WINDOW_HOURS}h"
    )

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=3000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )
    text = _strip_fences(response.content[0].text)
    parsed = json.loads(text)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input_window_hours": INPUT_WINDOW_HOURS,
        "input_post_count": len(recent),
        "model": CLAUDE_MODEL,
        "result": parsed,
    }


def load_cached(cache_file: Path) -> Optional[dict]:
    if not cache_file.exists():
        return None
    try:
        with cache_file.open(encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def save_cache(cache_file: Path, payload: dict) -> None:
    try:
        with cache_file.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
    except OSError as e:
        logger.warning(f"behavior: failed to save cache: {e}")


def is_fresh(cached: dict) -> bool:
    """True if the cache was generated within CACHE_TTL_MINUTES."""
    try:
        gen_at = datetime.fromisoformat(cached.get("generated_at", "").replace("Z", "+00:00"))
        if gen_at.tzinfo is None:
            gen_at = gen_at.replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        return False
    return datetime.now(timezone.utc) - gen_at < timedelta(minutes=CACHE_TTL_MINUTES)
