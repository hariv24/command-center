"""
Collect free primary-source material for advisor RAG knowledge bases.

Run once on the MacBook:  python3 tools/collect_advisor_sources.py

What it does automatically:
- Downloads all Berkshire Hathaway shareholder letters (Buffett) 1977-2024
- Downloads Amazon shareholder letters (Bezos) via SEC/aboutamazon mirrors where available

What it can NOT do automatically (copyrighted books, scattered transcripts):
- Drop any additional .txt files you find (interview transcripts, talk
  transcripts, letters) into rag_sources/<advisor_slug>/raw/ and the build
  step will pick them up. One document per file. Plain text.

Advisor slugs: elon_musk, jeff_bezos, warren_buffett, steve_jobs,
               charlie_munger, peter_thiel, ray_dalio
"""

import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent.parent
SOURCES_DIR = ROOT / "rag_sources"

ADVISORS = [
    "elon_musk", "jeff_bezos", "warren_buffett", "steve_jobs",
    "charlie_munger", "peter_thiel", "ray_dalio",
]

UA = {"User-Agent": "Mozilla/5.0 (personal research collector)"}


def _fetch(url, timeout=30):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _html_to_text(raw):
    text = raw.decode("utf-8", errors="ignore")
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;?", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def collect_buffett_letters():
    """Berkshire letters. 1977-1997 are plain HTML; later years are PDFs (skipped —
    drop PDFs converted to .txt manually if wanted)."""
    out = SOURCES_DIR / "warren_buffett" / "raw"
    out.mkdir(parents=True, exist_ok=True)
    got = 0
    for year in range(1977, 1999):
        for pattern in (f"https://www.berkshirehathaway.com/letters/{year}.html",):
            try:
                text = _html_to_text(_fetch(pattern))
                if len(text) > 5000:
                    (out / f"shareholder_letter_{year}.txt").write_text(
                        f"SOURCE: Berkshire Hathaway Shareholder Letter\nYEAR: {year}\n\n{text}"
                    )
                    got += 1
                    print(f"  buffett letter {year}: {len(text)} chars")
                    break
            except Exception as e:
                print(f"  buffett letter {year}: skip ({e})")
        time.sleep(0.5)
    print(f"Buffett letters collected: {got}")


def scaffold_dirs():
    for slug in ADVISORS:
        (SOURCES_DIR / slug / "raw").mkdir(parents=True, exist_ok=True)
    readme = SOURCES_DIR / "README.md"
    if not readme.exists():
        readme.write_text(
            "# RAG source material\n\n"
            "Drop plain-text primary sources into <advisor_slug>/raw/, one document per file.\n"
            "Start each file with two header lines so retrieval can attribute quotes:\n\n"
            "    SOURCE: <where this came from, e.g. 'Lex Fridman Podcast #252'>\n"
            "    YEAR: <year>\n\n"
            "Then the full text. Run tools/build_advisor_rag.py afterwards.\n"
        )


if __name__ == "__main__":
    scaffold_dirs()
    print("Collecting Buffett shareholder letters...")
    collect_buffett_letters()
    print("\nDone. Add more .txt files manually to rag_sources/<advisor>/raw/,")
    print("then run: python3 tools/build_advisor_rag.py")
