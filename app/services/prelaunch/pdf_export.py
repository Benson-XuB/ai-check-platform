"""HTML → PDF（WeasyPrint 可选）。"""

from pathlib import Path


def html_to_pdf(html: str, out_pdf: Path) -> bool:
    try:
        from weasyprint import HTML
    except ImportError:
        return False
    try:
        out_pdf.parent.mkdir(parents=True, exist_ok=True)
        HTML(string=html, base_url=str(out_pdf.parent)).write_pdf(str(out_pdf))
        return out_pdf.is_file() and out_pdf.stat().st_size > 0
    except Exception:
        return False
