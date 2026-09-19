"""The interface must not expose pipeline internals.

The backend runs thirteen graph nodes with names like "dossier" and "audit".
Those are engineering concepts. A marketer waiting on a shortlist should see
plain phases, not a system status page.
"""
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web" / "src"

# Words that only make sense if you have read the backend.
JARGON = [
    "dossier", "Dossier",
    "agent_failures",
    "relevance_judge", "audience_analyst", "safety_auditor",
    "sponsorship_analyst", "cultural_analyst", "output_auditor",
    "Verifying every claim",
    "Building dossiers",
]


def _ui_text() -> str:
    """Rendered copy from the components, excluding code identifiers."""
    parts = []
    for f in (WEB / "components").glob("*.tsx"):
        parts.append(f.read_text())
    parts.append((WEB / "App.tsx").read_text())
    return "\n".join(parts)


def test_progress_view_shows_phases_not_pipeline_nodes():
    src = (WEB / "components" / "ProgressView.tsx").read_text()
    for word in ("dossier", "Building dossiers", "Verifying every claim"):
        assert word not in src, f"pipeline internal {word!r} is user-visible"


def test_phase_labels_are_plain_language():
    import re
    phases = (WEB / "lib" / "phases.ts").read_text()
    # Only the label strings matter; comments may name the internals they hide.
    labels = re.findall(r'label:\s*"([^"]+)"', phases)
    assert 3 <= len(labels) <= 6, f"expected a handful of phases, found {labels}"
    for label in labels:
        for word in JARGON:
            assert word.lower() not in label.lower(), \
                f"{word!r} leaked into the phase label {label!r}"
        assert len(label) < 34, f"phase label is too long: {label!r}"


def test_phase_coverage_reaches_the_final_node():
    """Every step count from 0 to 13 must map to a phase, with none skipped."""
    phases = (WEB / "lib" / "phases.ts").read_text()
    uptos = [int(n) for n in
             __import__("re").findall(r"upto:\s*(\d+)", phases)]
    assert uptos == sorted(uptos), "phase thresholds are out of order"
    # The final phase must cover the last graph node, whatever the count is.
    import api.main as m
    assert uptos[-1] >= len(m.NODE_LABELS), (
        f"final phase covers {uptos[-1]} but the graph has "
        f"{len(m.NODE_LABELS)} nodes, so progress would stall short"
    )
