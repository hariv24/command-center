"""
One-off ingestion of manually-sourced material dropped into content/.
Converts PDFs/txt into rag_sources/<advisor>/raw/*.txt with SOURCE/YEAR headers,
then you run tools/build_advisor_rag.py as usual.

Run with the RAG venv (has pypdf):  .rag_venv/bin/python tools/ingest_content_folder.py
"""

import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
CONTENT_DIR = ROOT / "content"
SOURCES_DIR = ROOT / "rag_sources"


def _pdf_to_text(path):
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    return "\n".join(page.extract_text() or "" for page in reader.pages).strip()


def _clean(text):
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _write(slug, filename, source, year, text):
    out = SOURCES_DIR / slug / "raw"
    out.mkdir(parents=True, exist_ok=True)
    (out / filename).write_text(f"SOURCE: {source}\nYEAR: {year}\n\n{_clean(text)}")
    print(f"  {slug}/{filename}: {len(text)} chars")


def main():
    # Charlie Munger — Poor Charlie's Almanack
    p = CONTENT_DIR / "Poor Charlie’s Almanack_ The Wit and Wisdom of Charles T. Munger ( PDFDrive ).pdf"
    if p.exists():
        _write("charlie_munger", "poor_charlies_almanack.txt",
               "Poor Charlie's Almanack: The Wit and Wisdom of Charles T. Munger (book)", "2005",
               _pdf_to_text(p))

    # Ray Dalio — Principles
    p = CONTENT_DIR / "principles.pdf"
    if p.exists():
        _write("ray_dalio", "principles.txt",
               "Principles: Life and Work (book)", "2017", _pdf_to_text(p))

    # Peter Thiel — Zero to One
    p = CONTENT_DIR / "zero-to-one.pdf"
    if p.exists():
        _write("peter_thiel", "zero_to_one.txt",
               "Zero to One: Notes on Startups, or How to Build the Future (book)", "2014",
               _pdf_to_text(p))

    # Steve Jobs — collected interview excerpts (D8 2010, Fortune 2008, 1995 Lost Interview)
    p = CONTENT_DIR / "Steve Jobs Excerpts.txt"
    if p.exists():
        _write("steve_jobs", "collected_interview_excerpts.txt",
               "Collected interviews: D8 Conference 2010 (last interview), Fortune 2008, 1990s Lost Interview",
               "1990-2010", p.read_text(errors="ignore"))

    # Steve Jobs — Stanford commencement speech (PDF)
    p = CONTENT_DIR / "Steve-Jobs-Speech.pdf"
    if p.exists():
        _write("steve_jobs", "stanford_commencement_2005.txt",
               "Stanford Commencement Address", "2005", _pdf_to_text(p))

    # Steve Jobs + Bill Gates — D5 2007 joint interview
    p = CONTENT_DIR / "TRANSCRIPT–Bill Gates and Steve Jobs at .txt"
    if p.exists():
        _write("steve_jobs", "d5_2007_with_bill_gates.txt",
               "D5 Conference — joint interview with Bill Gates (Kara Swisher, Walt Mossberg)", "2007",
               p.read_text(errors="ignore"))

    # Elon Musk — Lex Fridman Podcast #438
    p = CONTENT_DIR / "elonmusk.txt"
    if p.exists():
        _write("elon_musk", "lex_fridman_438.txt",
               "Lex Fridman Podcast #438 — Neuralink and the Future of Humanity", "2024",
               p.read_text(errors="ignore"))

    # Elon Musk — Tesla earnings calls (txt + 8 PDFs)
    p = CONTENT_DIR / "Tesla, Inc.txt"
    if p.exists():
        _write("elon_musk", "tesla_earnings_q1_fy2026.txt",
               "Tesla Q1 FY2026 Earnings Call Transcript", "2026", p.read_text(errors="ignore"))

    for pdf in sorted(CONTENT_DIR.glob("TSLA_FY*.pdf")):
        m = re.match(r"TSLA_FY(\d{4})_Q(\d)", pdf.stem)
        year, q = (m.group(1), m.group(2)) if m else ("", "")
        _write("elon_musk", f"tesla_earnings_{pdf.stem.lower()}.txt",
               f"Tesla Q{q} FY{year} Earnings Call Transcript", year, _pdf_to_text(pdf))

    print("\nDone. Now run:  .rag_venv/bin/python tools/build_advisor_rag.py")


if __name__ == "__main__":
    main()
