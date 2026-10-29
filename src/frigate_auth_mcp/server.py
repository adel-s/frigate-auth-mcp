"""MCP server with authentication for Frigate NVR (0.17.x).

Talks to the AUTHENTICATED port (8971) only, logs in as a Frigate user (role `viewer`) and exposes
GET calls only: there is no tool that deletes, exports, edits the config or restarts anything.
The raw Frigate config is not exposed either - it carries camera stream URLs with credentials.

Environment:
    FRIGATE_URL         base URL of the authenticated port (default http://localhost:8971)
    FRIGATE_USER        Frigate username (required)
    FRIGATE_PASSWD      its password (required, secret, never logged)
    FRIGATE_TIMEOUT     seconds per request (default 30)
    FRIGATE_IMAGE_CAMERAS  comma-separated cameras whose IMAGES may be returned (snapshots, frames);
                        unset or empty = all. Text data (events, reviews) is not affected. Use it when the
                        MCP client sends images to a third-party model and indoor frames must not leave home.
"""

import logging
import os
from datetime import datetime, timedelta, timezone

import httpx
from mcp.server.fastmcp import FastMCP, Image

URL = os.environ.get("FRIGATE_URL", "http://localhost:8971").rstrip("/")
TIMEOUT = float(os.environ.get("FRIGATE_TIMEOUT", "30"))
IMAGE_CAMERAS = {c.strip() for c in os.environ.get("FRIGATE_IMAGE_CAMERAS", "").split(",") if c.strip()}

mcp = FastMCP("frigate")
logging.getLogger("httpx").setLevel(logging.WARNING)  # one INFO line per request otherwise
logging.getLogger("mcp.server.lowlevel.server").setLevel(logging.WARNING)  # same for every MCP request
_client = httpx.Client(base_url=URL, timeout=TIMEOUT)


def _login():
    # The only non-GET call: POST /api/login sets the `frigate_token` cookie (JWT) on the client.
    user, password = os.environ.get("FRIGATE_USER"), os.environ.get("FRIGATE_PASSWD")
    if not user or not password:
        raise RuntimeError("FRIGATE_USER and FRIGATE_PASSWD must be set")
    r = _client.post("/api/login", json={"user": user, "password": password})
    r.raise_for_status()


def _get(path, params=None):
    params = {k: v for k, v in (params or {}).items() if v is not None}
    r = _client.get(path, params=params)
    if r.status_code == 401:
        _login()
        r = _client.get(path, params=params)
    r.raise_for_status()
    return r


def _image_allowed(camera):
    if IMAGE_CAMERAS and camera not in IMAGE_CAMERAS:
        raise ValueError(f"Images from camera '{camera}' are disabled for this client (FRIGATE_IMAGE_CAMERAS)")


def _ts(value):
    # ISO 8601 (local or with offset) or unix seconds -> unix seconds for the Frigate API
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        dt = datetime.fromisoformat(value)
        return (dt if dt.tzinfo else dt.astimezone()).timestamp()


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).astimezone().isoformat(timespec="seconds") if ts else None


def _event(e):
    data = e.get("data") or {}
    return {
        "id": e["id"],
        "camera": e["camera"],
        "label": e["label"],
        "sub_label": e.get("sub_label"),
        "start": _iso(e.get("start_time")),
        "end": _iso(e.get("end_time")),
        "score": data.get("top_score") or e.get("top_score"),
        "zones": e.get("zones"),
        "has_clip": e.get("has_clip"),
        "has_snapshot": e.get("has_snapshot"),
        "description": data.get("description"),
    }


@mcp.tool()
def frigate_status() -> dict:
    """Frigate version, uptime, the cameras this user may see and per-camera fps / detection fps."""
    stats = _get("/api/stats").json()
    profile = _get("/api/profile").json()
    cams = {name: {k: c.get(k) for k in ("camera_fps", "detection_fps", "process_fps", "skipped_fps")}
            for name, c in (stats.get("cameras") or {}).items()}
    return {
        "version": _get("/api/version").text,
        "uptime_s": (stats.get("service") or {}).get("uptime"),
        "user": profile.get("username"),
        "role": profile.get("role"),
        "allowed_cameras": profile.get("allowed_cameras"),
        "cameras": cams,
    }


@mcp.tool()
def list_events(camera: str | None = None, label: str | None = None, after: str | None = None,
                before: str | None = None, limit: int = 20, has_description: bool = False) -> list[dict]:
    """Tracked-object events, newest first. after/before: ISO 8601 or unix seconds.
    label e.g. person, cat, squirrel. has_description=True keeps only events with a GenAI description."""
    # the API cannot filter on a description, so over-fetch and trim to `limit` here
    events = _get("/api/events", {
        "cameras": camera, "labels": label, "after": _ts(after), "before": _ts(before),
        "limit": limit * 10 if has_description else limit, "include_thumbnails": 0,
    }).json()
    out = [_event(e) for e in events]
    return [e for e in out if e["description"]][:limit] if has_description else out


@mcp.tool()
def get_event(event_id: str) -> dict:
    """One event in detail, including the GenAI description when there is one."""
    return _event(_get(f"/api/events/{event_id}").json())


@mcp.tool()
def event_snapshot(event_id: str, crop: bool = True) -> Image:
    """Snapshot of an event with its bounding box; crop=True zooms on the object."""
    _image_allowed(_get(f"/api/events/{event_id}").json()["camera"])
    r = _get(f"/api/events/{event_id}/snapshot.jpg", {"bbox": 1, "crop": int(crop), "height": 500})
    return Image(data=r.content, format="jpeg")


@mcp.tool()
def latest_frame(camera: str, height: int = 720) -> Image:
    """The current frame of a camera."""
    _image_allowed(camera)
    r = _get(f"/api/{camera}/latest.jpg", {"height": height})
    return Image(data=r.content, format="jpeg")


@mcp.tool()
def list_reviews(camera: str | None = None, severity: str | None = None, after: str | None = None,
                 before: str | None = None, limit: int = 20) -> list[dict]:
    """Review items (what the Frigate UI shows), newest first. severity: alert or detection.
    after/before: ISO 8601 or unix seconds; after defaults to 7 days ago (Frigate itself returns only the
    last 24 hours when it is not set)."""
    since = _ts(after) if after is not None else (datetime.now(timezone.utc) - timedelta(days=7)).timestamp()
    items = _get("/api/review", {
        "cameras": camera, "severity": severity, "after": since, "before": _ts(before), "limit": limit,
    }).json()
    return [{
        "id": i["id"],
        "camera": i["camera"],
        "severity": i.get("severity"),
        "start": _iso(i.get("start_time")),
        "end": _iso(i.get("end_time")),
        "objects": (i.get("data") or {}).get("objects"),
        "zones": (i.get("data") or {}).get("zones"),
        "audio": (i.get("data") or {}).get("audio"),
        "reviewed": i.get("has_been_reviewed"),
    } for i in items]


@mcp.tool()
def review_summary(days: int = 7) -> dict:
    """Counts of alerts and detections for the last 24 hours and per day for the last `days` days
    (reviewed / not reviewed), as in the Frigate UI."""
    summary = _get("/api/review/summary").json()
    day_keys = sorted((k for k in summary if k != "last24Hours"), reverse=True)[:days]
    return {k: summary[k] for k in ["last24Hours", *day_keys] if k in summary}


def main():
    mcp.run()


if __name__ == "__main__":
    main()
