"""启发式与 MVP 分级（无 LLM）。"""

from pathlib import Path

from app.services.prelaunch.heuristics.config import scan as config_scan
from app.services.prelaunch.mvp_buckets import apply_mvp_buckets, classify_finding
from app.services.prelaunch.schemas import NormalizedFinding


def test_classify_dependency_high():
    f = NormalizedFinding(
        id="a",
        severity="High",
        category="dependency",
        title="t",
        sources=["npm"],
    )
    assert classify_finding(f) == "blocking"


def test_config_heuristic_cors(tmp_path: Path):
    p = tmp_path / "srv.py"
    p.write_text("app.add_middleware(CORSMiddleware, allow_origins=[\"*\"])\n", encoding="utf-8")
    rows = config_scan(tmp_path)
    assert any("CORS" in r.title for r in rows)
    apply_mvp_buckets(rows)
    assert all(r.mvp_bucket for r in rows)
