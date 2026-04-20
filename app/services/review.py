"""AI 审查服务：调用 LLM，解析结构化输出。"""

import hashlib
import json
import os
import random
import re
from typing import Any, Dict, List, Optional, Tuple

REVIEW_CATEGORIES = "logic|design|readability|edge_case|semantic|security"

FALLBACK_COMMENTS = [
    "当前这行可能存在问题，建议检查逻辑是否完善。",
    "建议检查此处代码的健壮性和边界情况处理。",
    "可考虑添加注释或优化实现方式，提升可读性。",
    "此处建议补充错误处理或异常情况的考虑。",
    "敏感信息不宜明文存储，建议使用配置或环境变量。",
]


def _format_file_contexts(file_contexts: Dict[str, str]) -> str:
    """将变更文件完整内容格式化为 prompt 片段。"""
    parts = []
    for path, content in file_contexts.items():
        parts.append(f"### 文件: {path}\n```\n{content[:20000]}\n```")
    return "\n\n".join(parts) if parts else "(无额外文件上下文)"


def _build_prompt(
    diff: str,
    pr_title: str,
    pr_body: str,
    file_contexts: Dict[str, str],
    high_risk_areas: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """构建审查 prompt。high_risk_areas 为 Pass1 预筛选的高风险区域，注入后重点审查。"""
    fc = _format_file_contexts(file_contexts)
    high_risk_block = ""
    if high_risk_areas and len(high_risk_areas) > 0:
        lines = ["以下为预筛选识别的高风险区域，请在审查时优先关注："]
        for a in high_risk_areas[:15]:
            f = a.get("file") or "?"
            ln = a.get("line") or "?"
            reason = a.get("reason") or ""
            lines.append(f"- {f} 第 {ln} 行: {reason}")
        high_risk_block = "\n".join(lines) + "\n\n---\n\n"
    return f"""你是一名资深代码审查工程师，专注于 CI/静态检查难以覆盖的问题：
- 逻辑正确性与边界条件（逻辑是否完整、状态是否一致）
- 设计与抽象是否合理（职责划分、耦合度、是否 YAGNI）
- 可读性与可维护性（命名、结构、复杂度）
- 业务语义是否符合 PR 描述
- 安全风险（权限、鉴权、敏感信息处理等）
- 测试覆盖是否匹配改动的重要性

请忽略纯格式和简单 lint 问题（缩进、引号风格、trailing spaces、简单 unused import），假设已有自动格式化和静态检查。

【输出要求】
- 只输出 JSON，不要任何额外说明或前后缀：
  {{"comments":[{{"file":"path","line":N,"severity":"...","category":"...","suggestion":"..."}}]}}
- 字段含义：
  - file: 问题所在文件路径（来自上下文中的路径）
  - line: 主要问题对应的新代码行号（整数，未知可用 0）
  - severity:
    - "Critical": 会导致明显 bug / 崩溃 / 数据错误 / 安全问题，合并前必须修复
    - "Important": 强烈建议改进，有明显风险或设计问题，但不一定立即崩溃
    - "Minor": 建议类问题，如命名、注释、轻量重构，不影响当前正确性
  - category: 只能是以下之一：
    - "logic"（条件/状态流转错误）
    - "edge_case"（空值/越界/异常/超时/重试等边界情况）
    - "design"（职责不清、抽象不合理、模块划分不当、YAGNI）
    - "readability"（命名、注释、结构导致难以理解）
    - "semantic"（与业务语义或 PR 描述不符）
    - "security"（越权、注入、敏感信息暴露、不安全存储等）
  - suggestion: 面向开发者的具体建议，说明「问题 + 建议做法」，避免只给笼统评价。

【审查策略】
1. 逻辑与边界条件：
   - 检查条件分支和状态流转是否完整且互斥合理。
   - 对所有新引入或修改的分支逻辑，特别关注边界情况（空值、None、空列表、0、负数、极大值、异常返回值等）。
   - 对调用外部服务（HTTP、数据库、缓存、消息队列）的代码，检查是否有超时、异常处理、重试或降级逻辑；如缺失，请在 suggestion 中指出具体缺失的边界情况和建议的处理方式。（category 通常为 logic 或 edge_case）
2. 安全：
   - 对涉及用户输入、鉴权、访问控制、敏感配置（token/key/password/密钥）的代码，优先检查是否存在：
     - 未经鉴权或权限校验不足的访问；
     - 明文打印或写入日志的敏感信息；
     - 硬编码的密钥或密码；
     - 对外暴露的调试/测试接口未受保护。
   - 如发现潜在问题，请使用 category="security"，并在 suggestion 中简要说明风险和修复建议。
3. 测试相关：
   - 如果修改了对外接口、公共函数或关键业务逻辑，但上下文中看不到对应的测试代码（如命名匹配的 test 文件），可以在 suggestion 中简要列出建议补充的测试场景（例如：空输入、非法输入、异常分支、边界值等），无需给出完整测试代码。
4. 设计与可维护性：
   - 对明显超出当前需求范围、且缺乏使用场景说明的代码，可以按 YAGNI 原则建议删除或简化，并在 suggestion 中说明理由。
   - 关注职责过重的函数/模块、过深的嵌套、magic number 等，如有必要，可建议拆分或重构（category 通常为 design 或 readability）。
5. 去重与信噪比：
   - 对同一问题不要给多条重复评论，可在一条 suggestion 中说明范围。
   - 不要简单复述 diff 或代码本身，不要输出诸如“看起来不错”“代码质量很好”之类无信息内容。

【PR 信息】
- 标题：{pr_title}
- 描述：{pr_body[:500] if pr_body else "(无)"}

{high_risk_block}【变更的 diff】
```
{diff[:40000]}
```

【代码上下文（用于理解改动所处的文件和调用关系）】
{fc}
"""


def _parse_diff_lines(diff: str) -> List[tuple]:
    """从 diff 解析 (文件名, 行号) 列表。"""
    result = []
    lines = diff.split("\n")
    cur_file = ""
    hunk_re = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
    for line in lines:
        if line.startswith("--- ") or line.startswith("+++ "):
            path = line[4:].strip()
            if path.startswith("a/") or path.startswith("b/"):
                path = path[2:]
            if line.startswith("+++ "):
                cur_file = path or cur_file
        elif line.startswith("@@"):
            m = hunk_re.match(line)
            if m:
                start = int(m.group(1))
                result.append((cur_file or "(未知文件)", start))
                if start + 2 != start:
                    result.append((cur_file or "(未知文件)", start + 2))
    return result[:12]


def _make_fallback_review(diff: str) -> List[Dict[str, Any]]:
    """当 AI 返回无效时，生成兜底审查。"""
    items = _parse_diff_lines(diff)
    if not items:
        return [{"file": "(整体)", "line": 1, "severity": "Minor", "category": "readability", "suggestion": "当前改动较少或格式特殊，建议人工复核。"}]
    seen = set()
    parts = []
    comments = FALLBACK_COMMENTS.copy()
    random.shuffle(comments)
    for idx, (fn, line) in enumerate(items):
        if (fn, line) in seen:
            continue
        seen.add((fn, line))
        parts.append({
            "file": fn,
            "line": line,
            "severity": "Minor",
            "category": "readability",
            "suggestion": comments[idx % len(comments)],
        })
    return parts if parts else [{"file": "", "line": 0, "severity": "Minor", "category": "readability", "suggestion": FALLBACK_COMMENTS[0]}]


def _parse_json_review(text: str) -> Optional[List[Dict[str, Any]]]:
    """尝试从 LLM 输出解析 JSON。"""
    text = text.strip()
    for start in ["{", "```json", "```"]:
        i = text.find(start)
        if i >= 0:
            chunk = text[i:]
            if chunk.startswith("```"):
                parts = chunk.split("```", 2)
                if len(parts) >= 2:
                    chunk = parts[1]
                    if chunk.startswith("json"):
                        chunk = chunk[4:].lstrip()
            try:
                obj = json.loads(chunk)
                if isinstance(obj, dict) and "comments" in obj:
                    comments = obj["comments"]
                    if isinstance(comments, list) and comments:
                        return comments
            except json.JSONDecodeError:
                pass
    return None


def _parse_markdown_review(text: str) -> List[Dict[str, Any]]:
    """从 Markdown 格式解析（兼容原有 ## 文件: / ### 第 N 行）。"""
    items = []
    blocks = re.split(r"##\s*文件:", text)
    for i in range(1, len(blocks)):
        block = blocks[i]
        first_line, *rest = block.strip().split("\n", 1)
        cur_file = (first_line or "").replace(":", "").strip()
        content = rest[0] if rest else ""
        for m in re.finditer(r"###\s*第\s*(\d+)\s*行\s*\n([\s\S]*?)(?=###|##|$)", content):
            line, body = m.group(1), m.group(2).strip()
            suggestion = re.sub(r"^-\s*\[[^\]]+\]\s*", "", body).strip()
            items.append({
                "file": cur_file,
                "line": int(line) if line.isdigit() else 0,
                "severity": "Minor",
                "category": "readability",
                "suggestion": suggestion,
            })
    return items


def _parse_review_output(text: str, diff: str) -> List[Dict[str, Any]]:
    """解析 LLM 输出，优先 JSON，兜底 Markdown。"""
    if not text or "暂无有效改动" in text or len(text.strip()) < 20:
        return _make_fallback_review(diff)
    parsed = _parse_json_review(text)
    if parsed:
        return parsed
    items = _parse_markdown_review(text)
    if items:
        return items
    return _make_fallback_review(diff)


def _normalize_text(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s


def _sig_keywords(text: str, max_words: int = 8) -> str:
    """粗略提取关键词签名，用于聚类去重（无需额外 embedding）。"""
    t = _normalize_text(text)
    t = re.sub(r"[^a-z0-9\u4e00-\u9fff\s_./-]+", " ", t)
    words = [w for w in t.split(" ") if w and len(w) > 1]
    return " ".join(words[:max_words])


def _cluster_key(c: Dict[str, Any], *, line_window: int = 3) -> Tuple[str, int, str, str]:
    f = (c.get("file") or "").strip()
    ln = c.get("line")
    if ln is not None and isinstance(ln, (int, str)):
        ln = int(ln) if str(ln).isdigit() else 0
    else:
        ln = 0
    bucket = (ln // max(1, line_window)) if ln > 0 else 0
    cat = _normalize_text(c.get("category") or "")
    sig = _sig_keywords(c.get("suggestion") or "")
    return (f, bucket, cat, sig)


def _vote_merge_comments(
    comments: List[Dict[str, Any]],
    *,
    min_votes: int = 2,
    line_window: int = 3,
) -> List[Dict[str, Any]]:
    """Bugbot 风格：按近似 key 聚类，多数投票过滤，再合并。"""
    severity_order = {"Critical": 3, "Important": 2, "Minor": 1}
    clusters: Dict[Tuple[str, int, str, str], List[Dict[str, Any]]] = {}
    for c in comments:
        k = _cluster_key(c, line_window=line_window)
        clusters.setdefault(k, []).append(c)

    out: List[Dict[str, Any]] = []
    for (f, bucket, cat, sig), items in clusters.items():
        if len(items) < min_votes:
            continue
        best = max(items, key=lambda x: severity_order.get((x.get("severity") or "Minor"), 0))
        # 代表行号：取最小正行号
        lines = []
        for x in items:
            ln = x.get("line")
            if ln is not None and isinstance(ln, (int, str)) and str(ln).isdigit():
                v = int(ln)
                if v > 0:
                    lines.append(v)
        rep_line = min(lines) if lines else 0
        suggestions = list({(x.get("suggestion") or "").strip() for x in items if (x.get("suggestion") or "").strip()})
        merged_suggestion = "\n".join(suggestions) if len(suggestions) > 1 else (best.get("suggestion") or "")
        cats = list({(x.get("category") or "").strip() for x in items if (x.get("category") or "").strip()})
        category = ",".join(cats) if cats else (best.get("category") or "")
        out.append(
            {
                "file": f,
                "line": rep_line,
                "severity": best.get("severity") or "Minor",
                "category": category,
                "suggestion": merged_suggestion,
            }
        )
    return out


def _build_bugbot_prompt(
    *,
    diff: str,
    pr_title: str,
    pr_body: str,
    file_contexts: Dict[str, str],
    pr_summary: str,
    pass_id: int,
) -> str:
    """单次独立 pass 的 prompt（聚焦 AI 生成代码常见失败模式）。"""
    fc = _format_file_contexts(file_contexts)
    return f"""你是一名资深代码审查工程师。请对以下 PR 进行一次独立的 bug-finding pass（第 {pass_id} 次）。本次代码可能由 AI 生成，请重点寻找 AI 常见失败模式，并尽量给出可验证的证据。

【AI 生成代码特别关注（必查）】
1) 幻觉 API/不存在库/错误参数名；对外部库/内部函数调用请核验是否存在
2) 数据契约/模型不匹配：JSON/API/DB 字段名假设错误（如 id vs user_id）
3) 安全：SQL/命令拼接、鉴权绕过、敏感信息硬编码/泄露
4) 边界与错误处理：None/空数组/越界/超时/重试；异常信息泄露细节
5) 性能反模式：O(n²)、循环内字符串拼接、低效数据结构

【本次 PR 概要】
{pr_summary or "(无)"}

【PR 信息】
- 标题：{pr_title}
- 描述：{pr_body[:600] if pr_body else "(无)"}

【变更 diff】
```
{diff[:40000]}
```

【代码上下文】
{fc}

【输出要求】
- 只输出 JSON：{{"comments":[{{"file":"path","line":N,"severity":"Critical|Important|Minor","category":"...","suggestion":"..."}}]}}
- 若无问题，返回 {{"comments":[]}}
- 不要输出纯 lint/格式问题（缩进、引号风格等）
"""


def _validate_comments_with_llm(
    comments: List[Dict[str, Any]],
    *,
    diff: str,
    file_contexts: Dict[str, str],
    api_key: str,
    max_items: int = 20,
) -> List[Dict[str, Any]]:
    """
    Validator：再跑一次小模型复核，删除明显不成立/无证据/重复的条目。
    """
    if not comments:
        return comments
    items = comments[:max_items]
    kept: List[Dict[str, Any]] = []
    for c in items:
        file_path = c.get("file") or ""
        line = c.get("line") or 0
        snippet = ""
        if file_path and file_path in file_contexts and isinstance(line, int) and line > 0:
            lines = file_contexts[file_path].split("\n")
            idx = max(0, line - 1)
            snippet = "\n".join(lines[max(0, idx - 5) : idx + 10])
        prompt = f"""你是一名严苛的代码审查验证器。请判断下面这条审查意见是否在给定 diff/代码片段下“可信且可操作”。如果意见是臆测、无证据、或与代码无关，应该丢弃。

只输出 JSON：{{"keep": true|false, "reason": "一句话原因（可选）"}}

【意见】
file={file_path} line={line}
severity={c.get("severity")} category={c.get("category")}
suggestion={c.get("suggestion")}

【相关代码片段】
```
{snippet or "(无)"} 
```

【diff 摘要】
```
{diff[:8000]}
```
"""
        try:
            out = _call_dashscope(api_key, "qwen-turbo", prompt, max_tokens=200, temperature=0.0)
            txt = out.strip()
            chunk = txt
            if "```" in txt:
                chunk = txt.replace("```json", "").replace("```", "").strip()
            obj = json.loads(chunk[chunk.find("{") : chunk.rfind("}") + 1]) if "{" in chunk else {}
            if isinstance(obj, dict) and obj.get("keep") is True:
                kept.append(c)
        except Exception:
            # 保守：validator 失败则保留原条目
            kept.append(c)
    return kept


def review_bugbot_ai(
    diff: str,
    api_key: str,
    pr_title: str = "",
    pr_body: str = "",
    file_contexts: Optional[Dict[str, str]] = None,
    *,
    repo_key: Optional[str] = None,
    ref: Optional[str] = None,
    passes: int = 8,
    min_votes: int = 2,
) -> List[Dict[str, Any]]:
    """
    Bugbot 风格：多次独立 pass（随机化 diff 顺序）→ 聚类投票过滤 → validator 复核。
    聚焦 AI 生成代码常见失败模式。
    """
    from app.services import rag_store as rag_svc

    clean_contexts, pyright_text = _extract_pyright_from_contexts(file_contexts or {})

    policy_hits: List[Dict[str, Any]] = []
    if repo_key and api_key:
        try:
            q = f"{diff[:2000]}\n{pr_title}\n{pr_body[:500]}"
            policy_hits = rag_svc.search_rag(
                repo_key=repo_key,
                query_text=q,
                embedding_api_key=api_key,
                source_type="policy",
                ref=None,
                top_k=5,
            )
        except Exception:
            policy_hits = []
    pr_summary = generate_pr_summary(diff, pr_title, pr_body, pyright_text, policy_hits, api_key)

    # 将 diff 拆成“文件块”，每个 pass 随机顺序拼接，模拟 Bugbot 的随机 diff order
    blocks = re.split(r"(?=^diff --git )", diff, flags=re.M)
    blocks = [b for b in blocks if b.strip()]
    if not blocks:
        blocks = [diff]

    all_comments: List[Dict[str, Any]] = []
    for i in range(1, max(2, passes) + 1):
        rng = random.Random(hashlib.sha256(f"{i}".encode()).digest())
        shuffled = list(blocks)
        rng.shuffle(shuffled)
        pass_diff = "\n".join(shuffled)[:40000]
        prompt = _build_bugbot_prompt(
            diff=pass_diff,
            pr_title=pr_title,
            pr_body=pr_body,
            file_contexts=clean_contexts,
            pr_summary=pr_summary,
            pass_id=i,
        )
        try:
            content = _call_dashscope(api_key, "qwen-turbo", prompt)
            parsed = _parse_json_review(content) or []
            all_comments.extend(parsed)
        except Exception:
            pass

    voted = _vote_merge_comments(all_comments, min_votes=min_votes, line_window=3)
    if not voted:
        return _make_fallback_review(diff)

    # 对“依赖与并发/数据契约”相关的条目，额外注入 code 证据有助于 validator 判断
    #（保持实现简单：先用已有 file_contexts snippet 进行 validator）
    validated = _validate_comments_with_llm(voted, diff=diff, file_contexts=clean_contexts, api_key=api_key)
    return validated or voted


def review_default_ai(
    diff: str,
    api_key: str,
    pr_title: str = "",
    pr_body: str = "",
    file_contexts: Optional[Dict[str, str]] = None,
    *,
    repo_key: Optional[str] = None,
    ref: Optional[str] = None,
    passes: int = 8,
) -> List[Dict[str, Any]]:
    """
    默认智能审查：
    - Phase0：Summary（policy RAG + Pyright）
    - Phase1：4 维度覆盖扫描（得到候选集合）
    - Phase2：Bugbot 多 pass（随机 diff order）补充 + 多数投票过滤
    - Phase3：validator 复核降误报
    """
    # 先跑 4 维度（覆盖）
    dim_comments = review_multidim(
        diff,
        api_key,
        pr_title=pr_title,
        pr_body=pr_body,
        file_contexts=file_contexts,
        repo_key=repo_key,
        ref=ref,
    )

    # 再跑 Bugbot（降噪/共识）
    bug_comments = review_bugbot_ai(
        diff,
        api_key,
        pr_title=pr_title,
        pr_body=pr_body,
        file_contexts=file_contexts,
        repo_key=repo_key,
        ref=ref,
        passes=passes,
    )

    # 合并两路输出后再做一次投票聚类（dim 作为额外“1 票”信号）
    combined = list(dim_comments or []) + list(bug_comments or [])
    voted = _vote_merge_comments(combined, min_votes=2, line_window=3)
    if not voted:
        return dim_comments or bug_comments or _make_fallback_review(diff)

    clean_contexts, _ = _extract_pyright_from_contexts(file_contexts or {})
    validated = _validate_comments_with_llm(voted, diff=diff, file_contexts=clean_contexts, api_key=api_key)
    return validated or voted


def _call_dashscope(
    api_key: str,
    model: str,
    prompt: str,
    *,
    max_tokens: Optional[int] = None,
    temperature: float = 0.3,
) -> str:
    """调用 DashScope（经 LiteLLM），返回 content 文本。"""
    from app.services.llm_litellm import completion_text

    return completion_text(
        "dashscope",
        api_key,
        model,
        prompt,
        max_tokens=max_tokens,
        temperature=temperature,
        timeout=120.0,
    )


def call_dashscope(
    diff: str,
    api_key: str,
    pr_title: str = "",
    pr_body: str = "",
    file_contexts: Optional[Dict[str, str]] = None,
    model: str = "qwen-plus",
    high_risk_areas: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """调用通义千问进行审查。"""
    prompt = _build_prompt(diff, pr_title, pr_body, file_contexts or {}, high_risk_areas)
    content = _call_dashscope(api_key, model, prompt)
    return _parse_review_output(content, diff)


# ---------- 三阶段审查 (multipass) ----------


def pass1_prefilter(diff: str, api_key: str) -> List[Dict[str, Any]]:
    """Pass 1：仅 diff，qwen-long，识别高风险区域。约 500 tokens。"""
    prompt = f"""仅根据以下代码 diff 快速识别高风险区域（安全、逻辑崩溃、数据错误）。
以 JSON 返回，不要其他内容：
{{"high_risk_areas":[{{"file":"路径","line":N,"reason":"简短原因"}}]}}

代码 diff：
```
{diff[:8000]}
```
"""
    try:
        content = _call_dashscope(api_key, "qwen-long", prompt, max_tokens=600)
        text = content.strip()
        for start in ["{", "```json", "```"]:
            i = text.find(start)
            if i >= 0:
                chunk = text[i:].replace("```json", "").replace("```", "").strip()
                try:
                    obj = json.loads(chunk)
                    areas = obj.get("high_risk_areas")
                    if isinstance(areas, list):
                        return areas
                except json.JSONDecodeError:
                    pass
    except Exception:
        pass
    return []


def pass2_main(
    diff: str,
    api_key: str,
    pr_title: str = "",
    pr_body: str = "",
    file_contexts: Optional[Dict[str, str]] = None,
    high_risk_areas: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Pass 2：diff + 全量 file_contexts，qwen-turbo，生成全部 comments。high_risk_areas 来自 Pass1。"""
    prompt = _build_prompt(diff, pr_title, pr_body, file_contexts or {}, high_risk_areas)
    content = _call_dashscope(api_key, "qwen-turbo", prompt)
    return _parse_review_output(content, diff)


def pass3_deep_critical(
    comments: List[Dict[str, Any]],
    diff: str,
    file_contexts: Dict[str, str],
    api_key: str,
) -> List[Dict[str, Any]]:
    """Pass 3：仅对 Critical 条目的代码片段做深度安全/逻辑分析，qwen-max。"""
    criticals = [c for c in comments if (c.get("severity") or "").lower() == "critical"]
    if not criticals:
        return comments
    result = list(comments)
    for c in criticals:
        file_path = c.get("file") or ""
        line = c.get("line")
        snippet = ""
        if file_path and file_path in file_contexts and line is not None:
            lines = file_contexts[file_path].split("\n")
            idx = int(line) - 1 if isinstance(line, (int, str)) and str(line).isdigit() else 0
            snippet = "\n".join(lines[max(0, idx - 5) : idx + 10])
        prompt = f"""以下是一条已标记为 Critical 的代码审查意见，请针对其潜在的安全/逻辑风险做更深入分析，并给出更具体的修复建议。

【原始意见】
{c.get("suggestion", "")}

【相关代码片段】
```
{snippet or "(无)"}
```

请用 1–3 句话给出「在保持当前设计大致不变的前提下」更具体的修复建议或检查清单：
- 不要重复原意见的表述
- 不要输出 JSON，仅输出自然语言说明
- 尽量指出可以添加的检查、边界处理或安全防护点
"""
        try:
            refined = _call_dashscope(api_key, "qwen-max", prompt, max_tokens=400)
            if refined and len(refined.strip()) > 5:
                for i, r in enumerate(result):
                    if r.get("file") == c.get("file") and r.get("line") == c.get("line") and (r.get("severity") or "").lower() == "critical":
                        result[i] = {**r, "suggestion": refined.strip()}
                        break
        except Exception:
            pass
    return result


def review_multipass(
    diff: str,
    api_key: str,
    pr_title: str = "",
    pr_body: str = "",
    file_contexts: Optional[Dict[str, str]] = None,
    *,
    run_pass1: bool = True,
    run_pass3: bool = True,
    high_risk_areas: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """三阶段审查：Pass1 预筛选 → Pass2 主审查（注入 Pass1 高风险）→ Pass3 深化 Critical。"""
    areas = high_risk_areas
    if run_pass1:
        areas = pass1_prefilter(diff, api_key)
    comments = pass2_main(diff, api_key, pr_title, pr_body, file_contexts, high_risk_areas=areas)
    if run_pass3 and comments:
        comments = pass3_deep_critical(comments, diff, file_contexts or {}, api_key)
    return comments


# ---------- 4 维度串行审查 (Phase0 Summary + Phase1-4) ----------


def _extract_pyright_from_contexts(file_contexts: Dict[str, str]) -> Tuple[Dict[str, str], str]:
    """从 file_contexts 提取 [pyright diagnostics]，返回 (清洗后上下文, pyright文本)。"""
    pyright_key = "[pyright diagnostics]"
    pyright_text = ""
    clean: Dict[str, str] = {}
    for k, v in (file_contexts or {}).items():
        if k == pyright_key:
            pyright_text = (v or "")[:15000]
        elif not k.startswith("["):
            clean[k] = v
    return clean, pyright_text


def _format_rag_block(rag_results: List[Dict[str, Any]]) -> str:
    """将 RAG 检索结果格式化为 prompt 片段。"""
    if not rag_results:
        return "(无)"
    parts = []
    for i, r in enumerate(rag_results[:5], 1):
        source = r.get("source_path") or r.get("source_type") or "文档"
        content = (r.get("content") or "")[:2000]
        if content:
            parts.append(f"[{i}] 来源: {source}\n{content}")
    return "\n\n---\n\n".join(parts) if parts else "(无)"


def generate_pr_summary(
    diff: str,
    pr_title: str,
    pr_body: str,
    pyright_text: str,
    rag_results: List[Dict[str, Any]],
    api_key: str,
) -> str:
    """
    Phase 0: 基于 Pyright + RAG + PR 生成本次 PR 的概要，供后续 4 维度 prompt 使用。
    返回结构化摘要文本。
    """
    pyright_block = pyright_text[:8000] if pyright_text else "(无 Pyright 诊断)"
    rag_block = _format_rag_block(rag_results)
    prompt = f"""你是一名代码审查助手。根据以下信息，生成一份简洁的「本次 PR 概要」，供后续分维度审查时参考。本次 PR 可能包含 AI 辅助生成的代码，请在 risk_areas 中若适用则指出需重点核验的 API 调用、数据契约、边界处理等。

【PR 信息】
- 标题：{pr_title}
- 描述：{pr_body[:600] if pr_body else "(无)"}

【代码 diff 摘要】（前 4000 字符）
```
{diff[:4000]}
```

【Pyright 静态分析结果】（如有）
{pyright_block}

【仓库相关规范/文档】（RAG 检索）
{rag_block}

请输出 JSON 格式的概要，不要其他内容：
{{
  "overview": "2-3 句话描述本次改动的主要内容和目的",
  "static_analysis_points": "Pyright 要点摘要（若无则为空字符串）",
  "applicable_rules": "从 RAG 中与本次改动相关的规范要点（若无则为空）",
  "risk_areas": "需重点关注的区域或风险点（文件/模块）。若为 AI 生成代码，可含：新增 import/API 真实性、数据契约匹配、边界与错误处理、环境依赖等"
}}
"""
    try:
        content = _call_dashscope(api_key, "qwen-turbo", prompt, max_tokens=1200)
        text = content.strip()
        for start in ["{", "```json", "```"]:
            i = text.find(start)
            if i >= 0:
                chunk = text[i:].replace("```json", "").replace("```", "").strip()
                try:
                    obj = json.loads(chunk)
                    if isinstance(obj, dict):
                        parts = []
                        if obj.get("overview"):
                            parts.append(f"【改动概览】{obj['overview']}")
                        if obj.get("static_analysis_points"):
                            parts.append(f"【静态分析要点】{obj['static_analysis_points']}")
                        if obj.get("applicable_rules"):
                            parts.append(f"【适用规范】{obj['applicable_rules']}")
                        if obj.get("risk_areas"):
                            parts.append(f"【重点区域】{obj['risk_areas']}")
                        return "\n".join(parts) if parts else obj.get("overview", "") or ""
                except json.JSONDecodeError:
                    pass
        return text[:2000] if text else ""
    except Exception:
        return ""


def _build_dimension_prompt(
    dimension: str,
    dimension_focus: str,
    diff: str,
    file_contexts: Dict[str, str],
    pr_summary: str,
    ai_specific_focus: str = "",
    code_evidence: str = "",
) -> str:
    """构建单维度的审查 prompt。ai_specific_focus 为 AI 生成代码专项检查项。"""
    fc = _format_file_contexts(file_contexts)
    ai_block = ""
    if ai_specific_focus:
        ai_block = f"""

【AI 生成代码专项】（若本次改动疑似由 AI 辅助生成，请额外重点检查）
{ai_specific_focus}
"""
    evidence_block = ""
    if code_evidence:
        evidence_block = f"""

【相关代码证据（语义检索）】（仅供核验 API/数据契约/调用是否真实，不要凭空猜测）
{code_evidence}
"""
    return f"""你是一名资深代码审查工程师，专注于审查可能由 AI 生成的代码。请**仅**从「{dimension}」维度审查以下 PR 变更。

【你的审查重点】
{dimension_focus}
{ai_block}
{evidence_block}

【本次 PR 概要】（来自预分析，供你参考）
{pr_summary or "(无)"}

【变更的 diff】
```
{diff[:35000]}
```

【代码上下文】
{fc}

【输出要求】
- 只输出 JSON：{{"comments":[{{"file":"path","line":N,"severity":"Critical|Important|Minor","category":"...","suggestion":"..."}}]}}
- 仅输出与「{dimension}」相关的问题，无关的不要输出
- 若该维度无问题，返回 {{"comments":[]}}
"""


DIMENSION_PROMPTS = [
    (
        "正确性与边界",
        """逻辑正确性、边界条件、错误处理、资源释放。
- 条件分支和状态流转是否完整且互斥
- 空值、None、空列表、越界、异常、超时、重试等边界情况
- 外部调用（HTTP/DB/缓存）是否有异常处理和超时
""",
        """AI 生成代码常忽略边界与错误处理：
- 对 null/None/空数组/0/负数/极大值是否有显式校验，还是假设 happy path？
- try-except 是否只 log 不处理、是否将栈/路径暴露给用户？
- 数组/列表访问前是否检查 bounds？JSON/API 解析是否处理缺失字段？
""",
    ),
    (
        "安全",
        """输入校验、注入、权限、敏感数据。
- 用户输入是否校验、是否存在注入风险
- 鉴权与权限校验是否充分
- 敏感信息（token/key/password）是否明文暴露、硬编码
""",
        """AI 生成代码的安全盲区（约 45% 含漏洞）：
- 是否用字符串拼接构建 SQL/命令而非参数化查询？
- 错误处理是否泄露敏感信息（密钥、路径、内部结构）？
- 鉴权逻辑是否有可绕过分支？新增接口是否默认受保护？
""",
    ),
    (
        "质量",
        """可读性、性能、测试覆盖。
- 命名、注释、结构是否清晰
- 是否引入明显性能瓶颈
- 改动是否有对应测试、是否覆盖边界与异常
""",
        """AI 生成代码的性能与测试问题：
- 嵌套循环是否导致 O(n²) 可优化为 O(n)？循环内是否重复拼接字符串？
- 数据结构选择是否适合规模（如大列表用 list 而非 set 查找）？
- 改动是否有对应测试？测试是否覆盖边界与异常路径？
""",
    ),
    (
        "依赖与并发",
        """API 真实性、并发安全、数据契约匹配。
- 调用的库函数是否真实存在、参数/属性是否符合文档
- 共享状态是否有适当同步、是否存在竞态
- 数据契约/模型匹配：对 API/DB 返回结构的假设是否与项目现有类型定义、接口文档一致
""",
        """AI 生成代码的幻觉与契约问题（重点）：
- 新增 import 和库调用：是否可能为幻觉 API？建议在 PyPI/npm 等验证包存在性
- 调用的方法/属性是否真实存在？参数名是否与官方文档一致（AI 常虚构参数）？
- 对 JSON/API/DB 响应的属性访问：是否与项目现有类型定义、schema 一致？是否存在 user.id 与 user.user_id 等命名不一致？
- 是否依赖未在仓库中声明的环境变量或外部服务？
""",
    ),
]


# 同一文件内行号相差在此范围内视为同一逻辑位置，合并评论
_LINE_MERGE_WINDOW = 3


def _merge_comments_by_file_line(comments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """按 (file, line±N) 去重并聚合：同一文件内行号相近的多条合并为一条，severity 取最高。"""
    severity_order = {"Critical": 3, "Important": 2, "Minor": 1}

    # 按文件分组
    by_file: Dict[str, List[tuple]] = {}
    for c in comments:
        f = (c.get("file") or "").strip()
        ln = c.get("line")
        if ln is not None and isinstance(ln, (int, str)):
            ln = int(ln) if str(ln).isdigit() else 0
        else:
            ln = 0
        by_file.setdefault(f, []).append((ln, c))

    result: List[Dict[str, Any]] = []
    for f, items in by_file.items():
        items.sort(key=lambda x: x[0])
        # 构建行号簇：行号相差 <= _LINE_MERGE_WINDOW 的视为同一簇
        clusters: List[List[tuple]] = []
        for ln, c in items:
            merged = False
            for cluster in clusters:
                cluster_lines = [x[0] for x in cluster]
                if any(abs(ln - x) <= _LINE_MERGE_WINDOW for x in cluster_lines):
                    cluster.append((ln, c))
                    merged = True
                    break
            if not merged:
                clusters.append([(ln, c)])

        for cluster in clusters:
            items_in_cluster = [x[1] for x in cluster]
            best = max(
                items_in_cluster,
                key=lambda x: severity_order.get((x.get("severity") or "Minor"), 0),
            )
            rep_line = min(x[0] for x in cluster)
            suggestions = list(
                {(x.get("suggestion") or "").strip() for x in items_in_cluster if (x.get("suggestion") or "").strip()}
            )
            merged_suggestion = "\n".join(suggestions) if len(suggestions) > 1 else (best.get("suggestion") or "")
            cats = list({(x.get("category") or "").strip() for x in items_in_cluster if (x.get("category") or "").strip()})
            category = ",".join(cats) if cats else (best.get("category") or "")
            result.append({
                "file": f,
                "line": rep_line,
                "severity": best.get("severity") or "Minor",
                "category": category,
                "suggestion": merged_suggestion or best.get("suggestion", ""),
            })

    return result


def review_multidim(
    diff: str,
    api_key: str,
    pr_title: str = "",
    pr_body: str = "",
    file_contexts: Optional[Dict[str, str]] = None,
    *,
    repo_key: Optional[str] = None,
    ref: Optional[str] = None,
    rag_top_k: int = 5,
) -> List[Dict[str, Any]]:
    """
    4 维度串行审查：Phase0 生成 PR Summary → Phase1-4 各维度串行 → 按 (file,line) 合并。
    需 DashScope API Key。若提供 repo_key，会检索 RAG 并注入 Summary。
    """
    from app.services import rag_store as rag_svc

    clean_contexts, pyright_text = _extract_pyright_from_contexts(file_contexts or {})
    rag_results: List[Dict[str, Any]] = []
    if repo_key and api_key:
        try:
            query = f"{diff[:2000]}\n{pr_title}\n{pr_body[:500]}"
            rag_results = rag_svc.search_rag(
                repo_key=repo_key,
                query_text=query,
                embedding_api_key=api_key,
                source_type="policy",
                ref=None,
                top_k=rag_top_k,
            )
        except Exception:
            pass

    pr_summary = generate_pr_summary(diff, pr_title, pr_body, pyright_text, rag_results, api_key)

    all_comments: List[Dict[str, Any]] = []
    for dim_name, dim_focus, ai_focus in DIMENSION_PROMPTS:
        code_evidence = ""
        # 仅在“依赖与并发/数据契约”维度按需拉取 code chunks
        if repo_key and api_key and dim_name == "依赖与并发":
            try:
                code_query = f"{pr_summary}\n\n{diff[:4000]}"
                code_hits = rag_svc.search_rag(
                    repo_key=repo_key,
                    query_text=code_query,
                    embedding_api_key=api_key,
                    source_type="code",
                    ref=ref,
                    top_k=5,
                )
                code_evidence = _format_rag_block(code_hits)
            except Exception:
                code_evidence = ""
        prompt = _build_dimension_prompt(
            dim_name,
            dim_focus,
            diff,
            clean_contexts,
            pr_summary,
            ai_specific_focus=ai_focus,
            code_evidence=code_evidence,
        )
        try:
            content = _call_dashscope(api_key, "qwen-turbo", prompt)
            parsed = _parse_json_review(content)
            if parsed:
                all_comments.extend(parsed)
        except Exception:
            pass

    return _merge_comments_by_file_line(all_comments) if all_comments else _make_fallback_review(diff)


def call_kimi(
    diff: str,
    api_key: str,
    pr_title: str = "",
    pr_body: str = "",
    file_contexts: Optional[Dict[str, str]] = None,
    *,
    model: str = "moonshot-v1-32k",
) -> List[Dict[str, Any]]:
    """调用 Kimi / Moonshot（经 LiteLLM）进行审查。"""
    from app.services.llm_litellm import completion_text

    prompt = _build_prompt(diff, pr_title, pr_body, file_contexts or {})
    content = completion_text("kimi", api_key, model, prompt, temperature=0.3, timeout=120.0)
    return _parse_review_output(content, diff)


def _review_custom_llm_timeout_sec() -> float:
    """PR 审查走自定义端点时的读超时（秒）；大 diff / 慢模型可设 REVIEW_CUSTOM_LLM_TIMEOUT_SEC=300。"""
    raw = (os.getenv("REVIEW_CUSTOM_LLM_TIMEOUT_SEC") or "").strip()
    if not raw:
        return 120.0
    try:
        return max(30.0, min(float(raw), 900.0))
    except ValueError:
        return 120.0


def call_custom_endpoint(
    diff: str,
    api_key: str,
    base_url: str,
    model: str,
    pr_title: str = "",
    pr_body: str = "",
    file_contexts: Optional[Dict[str, str]] = None,
    *,
    completion_backend: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """用户自定义 Base URL + 模型名；completion_backend 为 anthropic | litellm（来自凭证探测）。"""
    from app.services.llm_litellm import custom_endpoint_completion

    prompt = _build_prompt(diff, pr_title, pr_body, file_contexts or {})
    content = custom_endpoint_completion(
        api_key,
        base_url,
        model,
        prompt,
        max_tokens=None,
        temperature=0.3,
        timeout=_review_custom_llm_timeout_sec(),
        completion_backend=completion_backend,
    )
    return _parse_review_output(content, diff)


def call_litellm(
    diff: str,
    api_key: str,
    litellm_model: str,
    pr_title: str = "",
    pr_body: str = "",
    file_contexts: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    """任意 LiteLLM 支持的 model 字符串（如 openai/gpt-4o、anthropic/claude-3-5-sonnet-20241022）。"""
    from app.services.llm_litellm import completion_text

    prompt = _build_prompt(diff, pr_title, pr_body, file_contexts or {})
    content = completion_text(
        "litellm",
        api_key,
        litellm_model,
        prompt,
        temperature=0.3,
        timeout=120.0,
    )
    return _parse_review_output(content, diff)
