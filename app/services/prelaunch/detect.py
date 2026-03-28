"""根据仓库根目录探测语言与包管理器。"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import List


@dataclass
class ProjectProfile:
    root: Path
    has_python: bool = False
    has_node: bool = False
    has_java: bool = False
    has_javascript: bool = False
    has_maven: bool = False
    has_gradle: bool = False
    package_managers: List[str] = field(default_factory=list)
    lockfiles: List[str] = field(default_factory=list)


def _exists(root: Path, name: str) -> bool:
    return (root / name).is_file()


def detect_project(root: Path) -> ProjectProfile:
    root = root.resolve()
    p = ProjectProfile(root=root)
    if _exists(root, "requirements.txt") or _exists(root, "pyproject.toml") or _exists(root, "setup.py"):
        p.has_python = True
        p.package_managers.append("pip")
    if any(root.glob("*.py")) and not p.has_python:
        p.has_python = True

    if _exists(root, "package.json"):
        p.has_node = True
        p.has_javascript = True
        p.package_managers.append("npm")
    for name in ("package-lock.json", "npm-shrinkwrap.json"):
        if _exists(root, name):
            p.lockfiles.append(name)
    if _exists(root, "pnpm-lock.yaml"):
        p.lockfiles.append("pnpm-lock.yaml")
        p.has_node = True
        p.has_javascript = True
        if "pnpm" not in p.package_managers:
            p.package_managers.append("pnpm")
    if _exists(root, "yarn.lock"):
        p.lockfiles.append("yarn.lock")
        p.has_node = True
        p.has_javascript = True
        if "yarn" not in p.package_managers:
            p.package_managers.append("yarn")

    if _exists(root, "pom.xml"):
        p.has_java = True
        p.has_maven = True
        if "maven" not in p.package_managers:
            p.package_managers.append("maven")
    if _exists(root, "build.gradle") or _exists(root, "build.gradle.kts") or _exists(root, "settings.gradle") or _exists(
        root, "settings.gradle.kts"
    ):
        p.has_java = True
        p.has_gradle = True
        if "gradle" not in p.package_managers:
            p.package_managers.append("gradle")

    if not p.has_javascript:
        for _ in root.rglob("*.js"):
            if "node_modules" not in str(_.resolve()):
                p.has_javascript = True
                break
    if not p.has_javascript:
        for _ in root.rglob("*.ts"):
            if "node_modules" not in str(_.resolve()):
                p.has_javascript = True
                p.has_node = True
                break

    return p


def profile_hints_for_report(p: ProjectProfile) -> dict:
    """供 HTML 报告「上线前检查」区块展示的探测摘要。"""
    java_lines = []
    if p.has_maven:
        java_lines.append("已检测到 Maven（pom.xml）：依赖 CVE 由 trivy fs、npm_audit（若有前端）及 Semgrep Java 规则覆盖；完整 SBOM 可在构建机执行 mvn dependency:tree 后对接审计。")
    if p.has_gradle:
        java_lines.append("已检测到 Gradle：建议构建环境执行 ./gradlew dependencies 做依赖基线；本扫描含 trivy fs 与 Semgrep。")
    if p.has_java and not p.has_maven and not p.has_gradle:
        java_lines.append("发现 Java 相关文件但未在根目录识别 pom/build.gradle，可能是多模块子路径，建议确认构建入口。")
    stacks = []
    if p.has_java:
        stacks.append("Java")
    if p.has_node or p.has_javascript:
        stacks.append("Node/React/Vue")
    if p.has_python:
        stacks.append("Python")
    return {
        "stacks": stacks,
        "java_notes": java_lines,
        "lockfiles": list(p.lockfiles),
        "package_managers": list(p.package_managers),
    }
