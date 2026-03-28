"""Prelaunch Job 与 Finding 模型。"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    pending = "pending"
    cloning = "cloning"
    scanning = "scanning"
    normalizing = "normalizing"
    llm = "llm"
    rendering = "rendering"
    complete = "complete"
    failed = "failed"


class NormalizedFinding(BaseModel):
    id: str
    severity: str  # Critical|High|Medium|Low|Info
    category: str  # secret|dependency|sast|config|availability|other
    title: str
    file: str = ""
    line: int = 0
    snippet: str = ""
    sources: List[str] = Field(default_factory=list)
    raw_refs: Dict[str, Any] = Field(default_factory=dict)
    # MVP：上线阻断 / 可延后 / 非关键（启发式+严重度规则）
    mvp_bucket: str = ""  # blocking | later | info


class LlmReport(BaseModel):
    executive_summary: str = ""
    top_risks: List[str] = Field(default_factory=list)
    finding_notes: Dict[str, Dict[str, str]] = Field(default_factory=dict)
    architecture_section: str = ""
    compliance_checklist: List[Dict[str, Any]] = Field(default_factory=list)
    # Go/No-Go 三阶段流水线（Analyzer→Judge→Reporter）写入；旧版单轮 LLM 可留空
    verdict: Optional[str] = None  # go | no_go | unknown
    verdict_display: str = ""
    verdict_reasons: List[str] = Field(default_factory=list)  # No-Go/待定 的主要原因（1～3 条）
    optimize_suggestions: List[str] = Field(default_factory=list)  # Go 时的「建议优化」
    must_fix_before_launch: List[str] = Field(default_factory=list)
    fix_after_launch: List[str] = Field(default_factory=list)
    non_critical_notes: List[str] = Field(default_factory=list)  # 非关键/低优先级摘要
    # 详细报告四模块（Markdown）；空则回退到 architecture_section
    detail_security: str = ""
    detail_config: str = ""
    detail_dependency: str = ""
    detail_availability: str = ""


class PrelaunchJobRecord(BaseModel):
    job_id: str
    status: JobStatus = JobStatus.pending
    repo_url_display: str = ""
    ref: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""
    error: Optional[str] = None
    repo_path: Optional[str] = None
    raw_paths: Dict[str, Optional[str]] = Field(default_factory=dict)
    normalized_path: Optional[str] = None
    llm_report_path: Optional[str] = None
    html_report_path: Optional[str] = None
    pdf_report_path: Optional[str] = None
    normalized_count: int = 0

    def touch(self) -> None:
        self.updated_at = datetime.now(timezone.utc).isoformat()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
