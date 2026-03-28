"""上下文增强：测试文件关联、Python/Vue import 相关文件，合并进 file_contexts。"""

import ast
import re
from typing import Callable, Dict, List, Optional, Set

# 测试文件命名约定：(源文件) -> 候选测试路径
# Python: foo.py -> test_foo.py, foo_test.py, tests/test_foo.py, tests/foo_test.py
# JS/Vue: foo.js -> foo.test.js, foo.spec.js, __tests__/foo.js, tests/foo.test.js
#         Foo.vue -> Foo.spec.ts, __tests__/Foo.spec.ts


def get_test_candidate_paths(changed_files: List[str]) -> List[str]:
    """根据变更文件路径生成候选测试文件路径（去重、不重复已有）。"""
    seen: Set[str] = set()
    out: List[str] = []
    for path in changed_files:
        if not path or path in seen:
            continue
        dirname, basename = path.rsplit("/", 1) if "/" in path else ("", path)
        # 跳过已是测试文件的
        if _is_test_path(path):
            continue
        # Python
        if basename.endswith(".py") and not basename.startswith("__"):
            name = basename[:-3]
            for cand in (
                f"test_{name}.py",
                f"{name}_test.py",
                f"tests/test_{name}.py",
                f"tests/{name}_test.py",
                f"test/{name}_test.py",
            ):
                full = f"{dirname}/{cand}".lstrip("/") if dirname else cand
                if full not in seen:
                    seen.add(full)
                    out.append(full)
        # JS/TS/Vue
        if basename.endswith(".js") or basename.endswith(".ts") or basename.endswith(".vue"):
            name = basename.rsplit(".", 1)[0]
            for suffix in (".test.js", ".spec.js", ".test.ts", ".spec.ts"):
                for cand in (
                    f"{name}{suffix}",
                    f"__tests__/{name}{suffix}",
                    f"tests/{name}{suffix}",
                ):
                    full = f"{dirname}/{cand}".lstrip("/") if dirname else cand
                    if full not in seen:
                        seen.add(full)
                        out.append(full)
    return out


def _is_test_path(path: str) -> bool:
    p = path.lower()
    if "test" in p and (p.endswith(".py") or p.endswith(".js") or p.endswith(".ts")):
        if "test_" in p or "_test" in p or ".test." in p or ".spec." in p or "__tests__" in p:
            return True
    return False


def _python_imports_from_content(content: str) -> List[str]:
    """从 Python 源码中解析 import / from ... import，返回模块名列表（不含相对层级）。"""
    modules: List[str] = []
    try:
        tree = ast.parse(content)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name and not alias.name.startswith("."):
                        modules.append(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module and not (node.level or 0):
                    modules.append(node.module.split(".")[0])
                elif node.module:
                    modules.append(node.module.split(".")[0])
    except SyntaxError:
        pass
    return list(dict.fromkeys(modules))


# 简单正则：import x from 'y' / require('y')，以及 Vue 里 import 的路径
_JS_IMPORT_RE = re.compile(
    r'''(?:import\s+.*\s+from\s+['"]([^'"]+)['"]|require\s*\(\s*['"]([^'"]+)['"])''',
    re.MULTILINE,
)


def _js_import_paths_from_content(content: str, current_file_path: str) -> List[str]:
    """从 JS/TS/Vue 源码中解析 import/require 路径，返回可能同仓库内的相对路径。"""
    paths: List[str] = []
    dirname = current_file_path.rsplit("/", 1)[0] if "/" in current_file_path else ""
    for m in _JS_IMPORT_RE.finditer(content):
        raw = m.group(1) or m.group(2) or ""
        raw = raw.strip()
        if not raw or raw.startswith("http") or raw.startswith("/"):
            continue
        # 相对路径
        if raw.startswith("."):
            # 归一化：./foo -> dirname/foo, ../foo -> parent/foo
            parts = dirname.split("/") if dirname else []
            for seg in raw.split("/"):
                if seg == "..":
                    if parts:
                        parts.pop()
                elif seg and seg != ".":
                    parts.append(seg)
            if parts:
                paths.append("/".join(parts))
            continue
        # 别名/包名：可能对应 src/xxx 或 @/xxx
        if not raw.startswith("@") and "/" not in raw:
            for prefix in ("src/", ""):
                paths.append(f"{prefix}{raw}.js")
                paths.append(f"{prefix}{raw}.ts")
                paths.append(f"{prefix}{raw}/index.js")
                paths.append(f"{prefix}{raw}/index.ts")
    return list(dict.fromkeys(paths))[:20]


def get_import_related_paths(
    file_contexts: Dict[str, str],
    repo_tree_paths: Optional[List[str]] = None,
) -> List[str]:
    """
    从已有 file_contexts 中解析 Python/Vue 的 import，得到可能的相关文件路径。
    repo_tree_paths: 仓库在 ref 下的文件路径列表（来自 Gitee tree API），用于解析 Python 模块名。
    """
    to_fetch: Set[str] = set()
    for path, content in file_contexts.items():
        if not content:
            continue
        if path.endswith(".py"):
            modules = _python_imports_from_content(content)
            if repo_tree_paths:
                for mod in modules:
                    for p in repo_tree_paths:
                        if p == f"{mod}.py" or p.startswith(f"{mod}/"):
                            to_fetch.add(p)
            else:
                dirname = path.rsplit("/", 1)[0] if "/" in path else ""
                for mod in modules:
                    to_fetch.add(f"{dirname}/{mod}.py".lstrip("/") if dirname else f"{mod}.py")
                    to_fetch.add(f"{dirname}/{mod}/__init__.py".lstrip("/") if dirname else f"{mod}/__init__.py")
        elif path.endswith(".js") or path.endswith(".ts") or path.endswith(".vue"):
            for p in _js_import_paths_from_content(content, path):
                if p not in file_contexts:
                    to_fetch.add(p)
    return [p for p in to_fetch if p and p not in file_contexts]


def enrich_file_contexts(
    file_contexts: Dict[str, str],
    changed_files: List[str],
    fetch_file: Callable[[str], Optional[str]],
    *,
    add_tests: bool = True,
    add_imports: bool = True,
    repo_tree_paths: Optional[List[str]] = None,
) -> Dict[str, str]:
    """
    合并测试文件与 import 相关文件进 file_contexts。
    fetch_file(path) 返回该路径的文件内容，不存在则返回 None。
    """
    merged = dict(file_contexts)
    if add_tests:
        for path in get_test_candidate_paths(changed_files):
            if path in merged:
                continue
            content = fetch_file(path)
            if content:
                merged[path] = f"[关联测试文件]\n{content}"
    if add_imports:
        for path in get_import_related_paths(merged, repo_tree_paths):
            if path in merged:
                continue
            content = fetch_file(path)
            if content:
                merged[path] = f"[import 相关]\n{content}"
    return merged
