"""Quota accounting is an operator concern and must stay out of the UI.

The limits are still enforced (the session cap in api/main.py, the ledger in
cache.py), but a meter counting down only makes the user hesitate before
searching.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web" / "src"
API = ROOT / "api" / "main.py"


def frontend_text() -> str:
    parts = [(WEB / "App.tsx").read_text()]
    for f in (WEB / "components").glob("*.tsx"):
        parts.append(f.read_text())
    return "\n".join(parts)


def test_no_quota_counters_in_the_interface():
    text = frontend_text()
    for banned in (
        "search_calls", "units_left", "units_used", "quota",
        "Daily quota", "API units", "calls left",
    ):
        assert banned not in text, f"{banned!r} is user-visible again"


def test_no_password_gate():
    """The app is open; the session cap and ledger are the only guards."""
    assert "APP_PASSWORD" not in frontend_text()
    assert "APP_PASSWORD" not in API.read_text()


def test_limits_are_still_enforced_server_side():
    """Hiding the meter must not mean dropping the guard."""
    api = API.read_text()
    assert "SESSION_QUERY_CAP" in api          # per-session cap
    assert "quota_status" in api               # global ledger checked
    import cache
    assert cache.SEARCH_BLOCK_AT < cache.DAILY_SEARCH_CAP


def test_streamlit_is_fully_removed():
    """The Streamlit UI is archived; nothing should still import it.

    app.py exists again, but as the Vercel entrypoint that re-exports the
    FastAPI app, so this checks for Streamlit itself rather than a filename.
    """
    assert not (ROOT / "theme.py").exists()
    assert "streamlit" not in (ROOT / "requirements.txt").read_text().lower()
    root_app = ROOT / "app.py"
    if root_app.exists():
        assert "streamlit" not in root_app.read_text().lower()
