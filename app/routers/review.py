"""审查 API 路由：统一 LLM 审查入口。"""

from typing import Dict, Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.services import embedding as embedding_svc
from app.services.api_rate_limit import enforce_review_llm
from app.services.llm_defaults import get_public_default_llm_provider
from app.services import review as review_svc

router = APIRouter(prefix="/api", tags=["review"])


class ReviewRequest(BaseModel):
    diff: str
    pr_title: str = ""
    pr_body: str = ""
    file_contexts: Optional[Dict[str, str]] = None
    llm_provider: str = Field(default_factory=get_public_default_llm_provider)
    llm_api_key: str = ""
    use_mock: bool = False
    use_multipass: bool = False  # 三阶段审查：Pass1 预筛选 + Pass2 主审查 + Pass3 深化 Critical（仅 DashScope）
    use_semantic_context: bool = False  # 用 diff 向量检索 Top-K 相关代码片段并入上下文（仅 DashScope）
    use_dimension_review: bool = False  # 兼容：旧模式
    repo_key: Optional[str] = None  # owner/repo，用于 RAG 检索；use_dimension_review 时建议传入
    use_bugbot_review: bool = False  # 兼容：旧模式
    bugbot_passes: int = 8  # 兼容：旧模式
    ref: Optional[str] = None  # 可选：commit sha/branch，用于 code RAG 精确检索（建议传 head_sha）
    use_default_review: bool = True  # ✅ 默认智能审查：覆盖(4维度) + 投票/validator(Bugbot)
    default_passes: int = 8  # 默认模式下的独立 pass 次数（2-12）


@router.get("/public-config")
def public_config():
    """前端读取站点默认 LLM（无下拉框时）；密钥仍由用户自备。"""
    p = get_public_default_llm_provider()
    labels = {
        "dashscope": "通义千问 API Key（DashScope）",
        "kimi": "Kimi API Key（月之暗面）",
    }
    return {
        "default_llm_provider": p,
        "llm_key_label": labels.get(p, labels["dashscope"]),
        "supported_llm_providers": ["dashscope", "kimi"],
    }


MOCK_COMMENTS = [
    {"file": "config/settings.py", "line": 8, "severity": "Important", "category": "security", "suggestion": "敏感信息不应明文存储，建议使用环境变量或密钥管理服务。"},
    {"file": "src/utils/auth.py", "line": 23, "severity": "Important", "category": "security", "suggestion": "硬编码的密钥存在安全风险，建议迁移到配置中心。"},
]


@router.post("/review")
def review(request: Request, req: ReviewRequest):
    """统一审查入口：默认厂商由站点 PUBLIC_DEFAULT_LLM_PROVIDER 决定（默认通义）。"""
    if not req.use_mock:
        enforce_review_llm(request)
    if req.use_mock:
        return {"ok": True, "data": {"comments": MOCK_COMMENTS}}
    if not req.diff:
        return {"ok": False, "error": "缺少 diff"}
    if not req.llm_api_key:
        return {"ok": False, "error": "缺少 llm_api_key"}
    file_contexts = req.file_contexts or {}
    if req.use_semantic_context and req.llm_provider == "dashscope" and file_contexts and req.diff:
        try:
            file_contexts = embedding_svc.enrich_file_contexts_with_semantic_search(
                req.diff, file_contexts, req.llm_api_key
            )
        except Exception:
            pass
    try:
        if req.llm_provider == "kimi":
            comments = review_svc.call_kimi(
                req.diff,
                req.llm_api_key,
                pr_title=req.pr_title,
                pr_body=req.pr_body,
                file_contexts=file_contexts,
            )
        elif req.use_default_review and req.llm_provider == "dashscope":
            comments = review_svc.review_default_ai(
                req.diff,
                req.llm_api_key,
                pr_title=req.pr_title,
                pr_body=req.pr_body,
                file_contexts=file_contexts,
                repo_key=req.repo_key or None,
                ref=req.ref or None,
                passes=max(2, min(int(req.default_passes or 8), 12)),
            )
        elif req.use_dimension_review:
            comments = review_svc.review_multidim(
                req.diff,
                req.llm_api_key,
                pr_title=req.pr_title,
                pr_body=req.pr_body,
                file_contexts=file_contexts,
                repo_key=req.repo_key or None,
                ref=req.ref or None,
            )
        elif req.use_bugbot_review:
            comments = review_svc.review_bugbot_ai(
                req.diff,
                req.llm_api_key,
                pr_title=req.pr_title,
                pr_body=req.pr_body,
                file_contexts=file_contexts,
                repo_key=req.repo_key or None,
                ref=req.ref or None,
                passes=max(2, min(int(req.bugbot_passes or 8), 12)),
            )
        elif req.use_multipass:
            comments = review_svc.review_multipass(
                req.diff,
                req.llm_api_key,
                pr_title=req.pr_title,
                pr_body=req.pr_body,
                file_contexts=file_contexts,
            )
        else:
            comments = review_svc.call_dashscope(
                req.diff,
                req.llm_api_key,
                pr_title=req.pr_title,
                pr_body=req.pr_body,
                file_contexts=file_contexts,
            )
        return {"ok": True, "data": {"comments": comments}}
    except Exception as e:
        return {"ok": False, "error": str(e)}
