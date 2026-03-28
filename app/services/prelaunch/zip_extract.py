"""安全解压 ZIP 到任务 repo 目录（防 zip slip）。"""

import zipfile
from pathlib import Path


def extract_uploaded_zip(archive: Path, dest: Path) -> None:
    dest = dest.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, "r") as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = info.filename.replace("\\", "/").lstrip("/")
            if not name or ".." in Path(name).parts:
                continue
            target = (dest / name).resolve()
            try:
                target.relative_to(dest)
            except ValueError:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info, "r") as src:
                target.write_bytes(src.read())

    # 若仅一层根目录，提升到 dest（常见 GitHub zip）
    subs = [p for p in dest.iterdir() if p.name not in (".DS_Store",)]
    if len(subs) == 1 and subs[0].is_dir():
        inner = subs[0]
        for child in list(inner.iterdir()):
            target = dest / child.name
            if target.exists():
                continue
            child.rename(target)
        try:
            inner.rmdir()
        except OSError:
            pass
