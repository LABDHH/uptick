"""SQLite cache + quota ledger.

A performance layer, not a warehouse: TTLs are deliberately short because the
YouTube Developer Policies restrict how long API data may be retained.

Note on Streamlit Community Cloud: the filesystem is ephemeral, so this DB is
wiped on redeploy. That is survivable for v1 (the cache simply rebuilds).
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

def _default_db_path() -> str:
    """Where SQLite lives.

    Streamlit Community Cloud gives each app a writable but EPHEMERAL
    filesystem: everything here is wiped on every redeploy and on the periodic
    container recycle. That is survivable because this is a cache, not a
    warehouse — it simply rebuilds. Honour an env var so a deployment with a
    real disk (or /tmp) can point somewhere sensible.
    """
    return os.environ.get("UPTICK_DB", "uptick.db")


DB_PATH = _default_db_path()

SEARCH_TTL = 6 * 3600        # 6h
CHANNEL_TTL = 24 * 3600      # 24h
VIDEO_TTL = 24 * 3600        # 24h
AGENT_TTL = 24 * 3600        # 24h

# §1 hard limits
DAILY_SEARCH_CAP = 100
SEARCH_BLOCK_AT = 90         # block before we hit the real ceiling
DAILY_UNIT_CAP = 10_000

_local = threading.local()

# Pacific standard offset. YouTube quota resets at midnight Pacific; we use a
# fixed -8 offset rather than -7/-8 DST-correct time. The consequence of being
# an hour off is at most one hour of over-conservative blocking, which is the
# safe direction to err.
_PACIFIC = timezone(timedelta(hours=-8))


def pacific_date() -> str:
    """Quota resets at midnight *Pacific*, never local time."""
    return datetime.now(_PACIFIC).strftime("%Y-%m-%d")


def _conn() -> sqlite3.Connection:
    c = getattr(_local, "conn", None)
    if c is None:
        c = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        _local.conn = c
    return c


def init_db() -> None:
    c = _conn()
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS search_cache (
            query_hash TEXT PRIMARY KEY,
            response_json TEXT NOT NULL,
            fetched_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS channel_cache (
            channel_id TEXT PRIMARY KEY,
            data_json TEXT NOT NULL,
            uploads_playlist TEXT,
            fetched_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS video_cache (
            video_id TEXT PRIMARY KEY,
            channel_id TEXT,
            data_json TEXT NOT NULL,
            duration_s INTEGER,
            published_at TEXT,
            fetched_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS agent_cache (
            dossier_hash TEXT NOT NULL,
            agent_name TEXT NOT NULL,
            response_json TEXT NOT NULL,
            created_at REAL NOT NULL,
            PRIMARY KEY (dossier_hash, agent_name)
        );
        CREATE TABLE IF NOT EXISTS quota_ledger (
            date_pt TEXT PRIMARY KEY,
            search_calls_used INTEGER NOT NULL DEFAULT 0,
            units_used INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS agent_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT,
            agent_name TEXT,
            latency_ms INTEGER,
            status TEXT,
            created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_video_channel ON video_cache(channel_id);
        CREATE INDEX IF NOT EXISTS idx_agentlog_run ON agent_log(run_id);
        """
    )
    c.commit()


# ---------------------------------------------------------------- generic get/put

def _get(table: str, key_col: str, key: str, ttl: int) -> Optional[dict]:
    row = _conn().execute(
        f"SELECT * FROM {table} WHERE {key_col} = ?", (key,)
    ).fetchone()
    if row is None:
        return None
    if time.time() - row["fetched_at"] > ttl:
        return None
    return dict(row)


def get_search(query_hash: str) -> Optional[Any]:
    row = _get("search_cache", "query_hash", query_hash, SEARCH_TTL)
    return json.loads(row["response_json"]) if row else None


def put_search(query_hash: str, response: Any) -> None:
    c = _conn()
    c.execute(
        "INSERT OR REPLACE INTO search_cache VALUES (?,?,?)",
        (query_hash, json.dumps(response), time.time()),
    )
    c.commit()


def get_channels(ids: list[str]) -> dict[str, dict]:
    """Return only the channels still within TTL. Callers fetch the rest."""
    if not ids:
        return {}
    out: dict[str, dict] = {}
    cutoff = time.time() - CHANNEL_TTL
    qmarks = ",".join("?" * len(ids))
    rows = _conn().execute(
        f"SELECT * FROM channel_cache WHERE channel_id IN ({qmarks}) AND fetched_at > ?",
        (*ids, cutoff),
    ).fetchall()
    for r in rows:
        out[r["channel_id"]] = json.loads(r["data_json"])
    return out


def put_channels(channels: list[dict]) -> None:
    c = _conn()
    now = time.time()
    c.executemany(
        "INSERT OR REPLACE INTO channel_cache VALUES (?,?,?,?)",
        [
            (
                ch["id"],
                json.dumps(ch),
                ch.get("contentDetails", {})
                  .get("relatedPlaylists", {})
                  .get("uploads"),
                now,
            )
            for ch in channels
            if ch.get("id")
        ],
    )
    c.commit()


def get_videos(ids: list[str]) -> dict[str, dict]:
    if not ids:
        return {}
    out: dict[str, dict] = {}
    cutoff = time.time() - VIDEO_TTL
    # SQLite caps host parameters; chunk to stay well under it.
    for i in range(0, len(ids), 400):
        chunk = ids[i : i + 400]
        qmarks = ",".join("?" * len(chunk))
        rows = _conn().execute(
            f"SELECT * FROM video_cache WHERE video_id IN ({qmarks}) AND fetched_at > ?",
            (*chunk, cutoff),
        ).fetchall()
        for r in rows:
            out[r["video_id"]] = json.loads(r["data_json"])
    return out


def put_videos(videos: list[dict]) -> None:
    c = _conn()
    now = time.time()
    rows = []
    for v in videos:
        if not v.get("id"):
            continue
        rows.append(
            (
                v["id"],
                v.get("snippet", {}).get("channelId"),
                json.dumps(v),
                v.get("_duration_s"),
                v.get("snippet", {}).get("publishedAt"),
                now,
            )
        )
    c.executemany("INSERT OR REPLACE INTO video_cache VALUES (?,?,?,?,?,?)", rows)
    c.commit()


# ---------------------------------------------------------------- agent cache

def get_agent(dossier_hash: str, agent_name: str) -> Optional[Any]:
    row = _conn().execute(
        "SELECT response_json, created_at FROM agent_cache WHERE dossier_hash=? AND agent_name=?",
        (dossier_hash, agent_name),
    ).fetchone()
    if row is None or time.time() - row["created_at"] > AGENT_TTL:
        return None
    return json.loads(row["response_json"])


def put_agent(dossier_hash: str, agent_name: str, response: Any) -> None:
    c = _conn()
    c.execute(
        "INSERT OR REPLACE INTO agent_cache VALUES (?,?,?,?)",
        (dossier_hash, agent_name, json.dumps(response), time.time()),
    )
    c.commit()


# ---------------------------------------------------------------- quota ledger

def _ensure_today() -> str:
    d = pacific_date()
    c = _conn()
    c.execute("INSERT OR IGNORE INTO quota_ledger VALUES (?,0,0)", (d,))
    c.commit()
    return d


def quota_status() -> dict:
    d = _ensure_today()
    row = _conn().execute(
        "SELECT * FROM quota_ledger WHERE date_pt=?", (d,)
    ).fetchone()
    search_used = row["search_calls_used"]
    units_used = row["units_used"]
    return {
        "date_pt": d,
        "search_calls_used": search_used,
        "search_calls_left": max(0, SEARCH_BLOCK_AT - search_used),
        "units_used": units_used,
        "units_left": max(0, DAILY_UNIT_CAP - units_used),
        "blocked": search_used >= SEARCH_BLOCK_AT or units_used >= DAILY_UNIT_CAP,
    }


def can_spend(search_calls: int = 0, units: int = 0) -> bool:
    s = quota_status()
    if s["search_calls_used"] + search_calls > SEARCH_BLOCK_AT:
        return False
    if s["units_used"] + units > DAILY_UNIT_CAP:
        return False
    return True


def record_spend(search_calls: int = 0, units: int = 0) -> None:
    d = _ensure_today()
    c = _conn()
    c.execute(
        "UPDATE quota_ledger SET search_calls_used = search_calls_used + ?, "
        "units_used = units_used + ? WHERE date_pt = ?",
        (search_calls, units, d),
    )
    c.commit()


def mark_quota_exhausted() -> None:
    """Called on a real 403 quotaExceeded: stop everything until midnight PT."""
    d = _ensure_today()
    c = _conn()
    c.execute(
        "UPDATE quota_ledger SET units_used = ? WHERE date_pt = ?",
        (DAILY_UNIT_CAP, d),
    )
    c.commit()


# ---------------------------------------------------------------- agent log

def prune(max_age_days: int = 3) -> None:
    """Drop rows past their usefulness.

    Without this the agent log and the cache tables grow forever. TTLs already
    stop stale rows being *served*, but they are never deleted, and a long
    running deployment would keep a disk full of rows nothing can use. Also
    keeps us honest about YouTube's data-retention policy.
    """
    cutoff = time.time() - max_age_days * 86400
    c = _conn()
    for table in ("search_cache", "channel_cache", "video_cache"):
        c.execute(f"DELETE FROM {table} WHERE fetched_at < ?", (cutoff,))
    c.execute("DELETE FROM agent_cache WHERE created_at < ?", (cutoff,))
    c.execute("DELETE FROM agent_log WHERE created_at < ?", (cutoff,))
    c.execute("DELETE FROM quota_ledger WHERE date_pt < ?",
              ((datetime.now(_PACIFIC) - timedelta(days=7)).strftime("%Y-%m-%d"),))
    c.commit()


def log_agent(run_id: str, agent_name: str, latency_ms: int, status: str) -> None:
    c = _conn()
    c.execute(
        "INSERT INTO agent_log (run_id, agent_name, latency_ms, status, created_at) "
        "VALUES (?,?,?,?,?)",
        (run_id, agent_name, latency_ms, status, time.time()),
    )
    c.commit()


def agent_stats(limit: int = 200) -> list[dict]:
    rows = _conn().execute(
        "SELECT agent_name, status, latency_ms, created_at FROM agent_log "
        "ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]
