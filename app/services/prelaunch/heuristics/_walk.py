"""遍历源文件（跳过依赖与构建产物）。"""

from pathlib import Path
from typing import Iterator, Set

SKIP_DIR_NAMES: Set[str] = {
    "node_modules",
    ".git",
    "venv",
    ".venv",
    "__pycache__",
    "dist",
    "build",
    "target",
    ".gradle",
    ".idea",
    "coverage",
    ".next",
    ".nuxt",
}

TEXT_SUFFIXES = {
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".vue",
    ".java",
    ".kt",
    ".kts",
    ".go",
    ".rb",
    ".php",
    ".yml",
    ".yaml",
    ".json",
    ".toml",
    ".cfg",
    ".ini",
    ".properties",
    ".xml",
    ".env",
    ".md",
    ".html",
    ".css",
    ".scss",
}


def should_skip_dir(name: str) -> bool:
    return name in SKIP_DIR_NAMES or name.startswith(".")


def iter_text_files(repo: Path, *, max_bytes: int = 256_000) -> Iterator[Path]:
    for p in repo.rglob("*"):
        if not p.is_file():
            continue
        try:
            if p.stat().st_size > max_bytes:
                continue
        except OSError:
            continue
        if any(should_skip_dir(part) for part in p.relative_to(repo).parts[:-1]):
            continue
        name = p.name.lower()
        if name == ".env" or name.startswith(".env."):
            yield p
            continue
        suf = p.suffix.lower()
        if suf in TEXT_SUFFIXES or name in ("Dockerfile", "dockerfile"):
            yield p
