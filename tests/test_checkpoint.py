import pytest
import graph
from tests.test_graph import stub  # noqa: F401


@pytest.mark.asyncio
async def test_widen_loop_survives_the_checkpointer(tmp_path, monkeypatch):
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    monkeypatch.setattr(graph, "MAX_CANDIDATES", 5)
    monkeypatch.setattr(graph, "MIN_SCREENED", 1)
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "c.db")) as saver:
        g = graph.build_graph("k", "k", checkpointer=saver)
        cfg = {"configurable": {"thread_id": "t1"}, "recursion_limit": 60}
        async for _ in g.astream({"brief_raw": "protein brand India", "run_id": "t",
                                  "agent_failures": [], "notices": []}, config=cfg):
            pass
        st = (await g.aget_state(cfg)).values
    assert st.get("widen_round") == 1 and not st.get("fatal")
    assert len({c["channel_id"] for c in st["studied"]}) > 5
    assert st["rationales"] and st["scored"]
