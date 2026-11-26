"""Builds REVIEW.pdf (A4, one page) from REVIEW.md with headless Edge or Chrome.

Usage: python scripts/make_pdf.py   (needs the `markdown` and `pypdf` packages)
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import markdown
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parent.parent
BROWSERS = [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe", "google-chrome", "chromium"]
CSS = """
@page { size: A4; margin: 10mm 12mm; }
body { font-family: "Segoe UI", Arial, sans-serif; font-size: 9.3pt; line-height: 1.33; color: #111; }
h1 { font-size: 13.5pt; margin: 0 0 3pt; }
h2 { font-size: 10.2pt; margin: 6pt 0 1pt; border-bottom: 1px solid #ccc; padding-bottom: 1pt; }
p { margin: 2pt 0; }
ul, ol { margin: 1pt 0 2pt; padding-left: 14pt; }
li { margin: 0.5pt 0; }
code { font-family: Consolas, monospace; font-size: 8.6pt; background: #f2f2f2; padding: 0 2px; }
strong { font-weight: 600; }
"""


def main() -> None:
    md = (ROOT / "REVIEW.md").read_text(encoding="utf-8")
    body = markdown.markdown(md, extensions=["extra"])
    html = ROOT / "REVIEW.html"
    html.write_text(f"<!doctype html><html><head><meta charset='utf-8'><title>Moss Inference review</title>"
                    f"<style>{CSS}</style></head><body>{body}</body></html>", encoding="utf-8")
    pdf = ROOT / "REVIEW.pdf"
    browser = next((b for b in BROWSERS if Path(b).exists() or "\\" not in b), None)
    subprocess.run([browser, "--headless", "--disable-gpu", "--no-pdf-header-footer",
                    f"--print-to-pdf={pdf}", html.as_uri()], check=True, capture_output=True, timeout=120)
    html.unlink()
    pages = len(PdfReader(str(pdf)).pages)
    print(f"wrote {pdf.name}: {pages} page(s)")
    if pages != 1:
        sys.exit(1)


if __name__ == "__main__":
    main()
