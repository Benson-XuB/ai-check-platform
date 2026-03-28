"""从仓库中抽取少量关键文件片段，控制 token；不整仓塞给 LLM。"""

from pathlib import Path
from typing import List, Set, Tuple

from app.services.prelaunch.detect import ProjectProfile

SKIP_PARTS = {
    "node_modules",
    ".git",
    "venv",
    ".venv",
    "__pycache__",
    "dist",
    "build",
    "target",
    ".idea",
    ".gradle",
}

# 路径或文件名包含即提高优先级
HIGH_SIGNAL_SUBSTR = (
    "routes",
    "router",
    "/api/",
    "controller",
    "security",
    "auth",
    "cors",
    "middleware",
    "application.yml",
    "application.yaml",
    "application.properties",
    "docker-compose",
    "Dockerfile",
    "nginx",
)

HIGH_EXACT_NAMES = frozenset(
    {
        ".env",
        ".env.local",
        ".env.production",
        ".env.development",
        "dockerfile",
        "docker-compose.yml",
        "docker-compose.yaml",
        "vite.config.js",
        "vite.config.ts",
        "next.config.js",
        "next.config.mjs",
        "settings.py",
        "main.py",
        "app.py",
        "server.ts",
        "server.js",
    }
)


def _skip_path(p: Path, root: Path) -> bool:
    try:
        rel = p.relative_to(root)
    except ValueError:
        return True
    return any(part in SKIP_PARTS for part in rel.parts)


def _score_path(rel: Path) -> int:
    s = str(rel).lower()
    n = rel.name.lower()
    score = 0
    if n in HIGH_EXACT_NAMES:
        score += 10
    elif n.startswith(".env"):
        score += 8
    for sub in HIGH_SIGNAL_SUBSTR:
        if sub in s:
            score += 5
    ext = rel.suffix.lower()
    if ext in (".py", ".ts", ".tsx", ".js", ".jsx", ".java", ".vue", ".yml", ".yaml", ".properties", ".gradle", ".kts"):
        score += 1
    return score


def build_context_pack(
    repo_root: Path,
    profile: ProjectProfile,
    *,
    max_files: int = 28,
    max_chars_per_file: int = 6000,
    max_total_chars: int = 45000,
) -> str:
    repo_root = repo_root.resolve()
    candidates: List[Tuple[int, Path]] = []
    for p in repo_root.rglob("*"):
        if not p.is_file() or _skip_path(p, repo_root):
            continue
        if p.stat().st_size > 400_000:
            continue
        rel = p.relative_to(repo_root)
        sc = _score_path(rel)
        if sc > 0 or (profile.has_java and p.suffix.lower() == ".java"):
            candidates.append((sc, p))
    candidates.sort(key=lambda x: (-x[0], str(x[1])))
    seen: Set[str] = set()
    chunks: List[str] = []
    total = 0
    for _, p in candidates:
        key = str(p.resolve())
        if key in seen:
            continue
        seen.add(key)
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if len(text) > max_chars_per_file:
            text = text[:max_chars_per_file] + "\n… [truncated]"
        rel = p.relative_to(repo_root)
        block = f"### FILE: {rel}\n```\n{text}\n```\n"
        if total + len(block) > max_total_chars:
            break
        chunks.append(block)
        total += len(block)
        if len(chunks) >= max_files:
            break
    if not chunks:
        return "（未能抽取到高信号源文件；将主要依赖扫描器 JSON。）"
    return "\n".join(chunks)
