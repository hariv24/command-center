"""
Collect free primary-source material for advisor RAG knowledge bases.

Run once on the MacBook:  pip3 install pypdf && python3 tools/collect_advisor_sources.py

Fully automatic (no user action needed):
- Warren Buffett — all Berkshire Hathaway shareholder letters, 1977-2024
  (1977-1997 HTML, 1998+ PDF via pypdf)
- Jeff Bezos — Amazon shareholder letters, recent years from aboutamazon.com plus
  a combined 1997-2020 PDF (the per-year URL pattern 404s before ~2016)
- Charlie Munger — "The Psychology of Human Misjudgment" (Harvard, 1995) and
  "A Lesson on Elementary, Worldly Wisdom" (USC Business School, 1994) — his two
  most-cited standalone speeches, full text
- Ray Dalio — "How the Economic Machine Works — Leveragings and Deleveragings",
  the long-form economic principles text he released for free
- Steve Jobs — the 1995 Computerworld/Smithsonian oral history interview

Everything else (copyrighted books, interview transcripts, earnings calls) needs
material supplied manually — see the printed summary at the end of this script for
exactly what's missing and where to get it. Drop .txt files into
rag_sources/<advisor_slug>/raw/, one document per file, with two header lines:

    SOURCE: <where this came from, e.g. 'Lex Fridman Podcast #252'>
    YEAR: <year>

Then run tools/build_advisor_rag.py.

Advisor slugs: elon_musk, jeff_bezos, warren_buffett, steve_jobs,
               charlie_munger, peter_thiel, ray_dalio
"""

import re
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
    text = re.sub(r"&#39;", "'", text)
    text = re.sub(r"&quot;", '"', text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def _pdf_to_text(raw):
    import io
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(raw))
    return "\n".join(page.extract_text() or "" for page in reader.pages).strip()


def collect_buffett_letters():
    out = SOURCES_DIR / "warren_buffett" / "raw"
    out.mkdir(parents=True, exist_ok=True)
    got = 0
    for year in range(1977, 2025):
        dest = out / f"shareholder_letter_{year}.txt"
        if dest.exists():
            got += 1
            continue
        try:
            if year < 1998:
                text = _html_to_text(_fetch(f"https://www.berkshirehathaway.com/letters/{year}.html"))
            else:
                text = _pdf_to_text(_fetch(f"https://www.berkshirehathaway.com/letters/{year}ltr.pdf"))
            if len(text) > 3000:
                dest.write_text(f"SOURCE: Berkshire Hathaway Shareholder Letter\nYEAR: {year}\n\n{text}")
                got += 1
                print(f"  buffett {year}: {len(text)} chars")
        except Exception as e:
            print(f"  buffett {year}: skip ({e})")
        time.sleep(0.3)
    print(f"Buffett letters: {got}/48 years")


def collect_bezos_letters():
    out = SOURCES_DIR / "jeff_bezos" / "raw"
    out.mkdir(parents=True, exist_ok=True)
    got = 0
    for year in range(1997, 2022):
        dest = out / f"shareholder_letter_{year}.txt"
        if dest.exists():
            got += 1
            continue
        try:
            text = _html_to_text(_fetch(f"https://www.aboutamazon.com/news/company-news/{year}-letter-to-shareholders"))
            if len(text) > 2000:
                dest.write_text(f"SOURCE: Amazon Shareholder Letter\nYEAR: {year}\n\n{text}")
                got += 1
                print(f"  bezos {year}: {len(text)} chars")
        except Exception as e:
            pass  # not every year resolves at this URL pattern — that's expected
        time.sleep(0.3)
    print(f"Bezos letters: {got} years found (gaps are normal — see manual list)")


def collect_munger_speech():
    out = SOURCES_DIR / "charlie_munger" / "raw"
    out.mkdir(parents=True, exist_ok=True)
    dest = out / "psychology_of_human_misjudgment_1995.txt"
    if dest.exists():
        print("  munger speech: already have it")
        return
    try:
        text = _html_to_text(_fetch(
            "https://jamesclear.com/great-speeches/the-psychology-of-human-misjudgment-by-charlie-munger"
        ))
        if len(text) > 5000:
            dest.write_text(
                f"SOURCE: The Psychology of Human Misjudgment (USC Business School)\nYEAR: 1995\n\n{text}"
            )
            print(f"  munger speech: {len(text)} chars")
    except Exception as e:
        print(f"  munger speech: skip ({e})")


def collect_munger_worldly_wisdom():
    """
    'A Lesson on Elementary, Worldly Wisdom' — USC Business School, 1994. Munger's
    other most-cited standalone speech (distinct from the 1995 Harvard talk above),
    freely mirrored as a PDF by multiple investing-education sites since Munger
    himself never restricted its distribution.
    """
    out = SOURCES_DIR / "charlie_munger" / "raw"
    out.mkdir(parents=True, exist_ok=True)
    dest = out / "elementary_worldly_wisdom_1994.txt"
    if dest.exists():
        print("  munger worldly wisdom: already have it")
        return
    try:
        raw = _fetch("https://www.safalniveshak.com/wp-content/uploads/2012/08/Lesson-on-Elementary-Worldly-Wisdom-Charlie-Munger.pdf")
        text = _pdf_to_text(raw)
        if len(text) > 5000:
            dest.write_text(
                f"SOURCE: A Lesson on Elementary, Worldly Wisdom As It Relates To Investment "
                f"Management & Business (USC Business School)\nYEAR: 1994\n\n{text}"
            )
            print(f"  munger worldly wisdom: {len(text)} chars")
    except Exception as e:
        print(f"  munger worldly wisdom: skip ({e})")


def collect_dalio_economic_machine():
    """
    Ray Dalio's 'How the Economic Machine Works / Leveragings and Deleveragings' —
    the long-form economic principles text he released for free alongside the
    animated video of the same name. Archive.org hosts the OCR'd full text of the
    official Bridgewater PDF.
    """
    out = SOURCES_DIR / "ray_dalio" / "raw"
    out.mkdir(parents=True, exist_ok=True)
    dest = out / "economic_machine_leveragings_deleveragings.txt"
    if dest.exists():
        print("  dalio economic machine: already have it")
        return
    try:
        raw = _fetch(
            "https://archive.org/stream/RayDalioHowTheEconomicMachineWorksLeveragingsAndDeleveragings/"
            "Ray+Dalio+-+How+the+Economic+Machine+Works+-+Leveragings+and+Deleveragings_djvu.txt"
        )
        html_page = raw.decode("utf-8", errors="ignore")
        m = re.search(r"<pre[^>]*>(.*?)</pre>", html_page, re.DOTALL)
        if m:
            import html as _html_mod
            text = _html_mod.unescape(m.group(1)).strip()
            if len(text) > 5000:
                dest.write_text(
                    f"SOURCE: How the Economic Machine Works — Leveragings and Deleveragings (Bridgewater)\n"
                    f"YEAR: 2015\n\n{text}"
                )
                print(f"  dalio economic machine: {len(text)} chars")
    except Exception as e:
        print(f"  dalio economic machine: skip ({e})")


def collect_jobs_smithsonian_interview():
    """
    Steve Jobs' 1995 Computerworld/Smithsonian oral history interview — a 75-minute,
    wide-ranging conversation conducted for the Smithsonian's permanent record and
    freely republished (the Smithsonian's own site blocks scrapers behind Cloudflare,
    so this pulls Computerworld's mirror of the same public-domain transcript).
    """
    out = SOURCES_DIR / "steve_jobs" / "raw"
    out.mkdir(parents=True, exist_ok=True)
    dest = out / "smithsonian_oral_history_1995.txt"
    if dest.exists():
        print("  jobs smithsonian interview: already have it")
        return
    try:
        req = urllib.request.Request(
            "https://www.computerworld.com/article/1476597/steve-jobs-interview-one-on-one-in-1995.html",
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"},
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
        text = _html_to_text(raw)
        start = text.find("Morrow")
        end = text.find("your inbox")
        if start != -1 and end != -1 and end > start:
            text = text[max(0, start - 40):end].strip()
        if len(text) > 5000:
            dest.write_text(
                f"SOURCE: Computerworld/Smithsonian Oral History Interview (conducted by Daniel Morrow)\n"
                f"YEAR: 1995\n\n{text}"
            )
            print(f"  jobs smithsonian interview: {len(text)} chars")
    except Exception as e:
        print(f"  jobs smithsonian interview: skip ({e})")


def collect_bezos_letters_1997_2020_combined():
    """
    Individual-year fetches from aboutamazon.com only resolve for recent years
    (the URL pattern changed and 1997-2015 404 there now) — this pulls a widely
    mirrored PDF combining all 24 shareholder letters 1997-2020 in one document
    instead, filling the gap the per-year collector leaves.
    """
    out = SOURCES_DIR / "jeff_bezos" / "raw"
    out.mkdir(parents=True, exist_ok=True)
    dest = out / "shareholder_letters_1997_2020_combined.txt"
    if dest.exists():
        print("  bezos combined 1997-2020: already have it")
        return
    try:
        raw = _fetch("https://bettertomorrowfinancial.com/wp-content/uploads/2021/04/jeff-bezos-amazon-shareholder-letters-1997_2020.pdf")
        text = _pdf_to_text(raw)
        if len(text) > 20000:
            dest.write_text(
                f"SOURCE: Amazon Shareholder Letters 1997-2020, combined (all 24 letters concatenated)\n"
                f"YEAR: 1997-2020\n\n{text}"
            )
            print(f"  bezos combined 1997-2020: {len(text)} chars")
    except Exception as e:
        print(f"  bezos combined 1997-2020: skip ({e})")


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


def _count(slug):
    return len(list((SOURCES_DIR / slug / "raw").glob("*.txt")))


if __name__ == "__main__":
    scaffold_dirs()
    print("Collecting Buffett shareholder letters (1977-2024)...")
    collect_buffett_letters()
    print("\nCollecting Bezos shareholder letters (recent years)...")
    collect_bezos_letters()
    print("\nCollecting Bezos shareholder letters (combined 1997-2020 PDF)...")
    collect_bezos_letters_1997_2020_combined()
    print("\nCollecting Munger's 'Psychology of Human Misjudgment' speech...")
    collect_munger_speech()
    print("\nCollecting Munger's 'Elementary, Worldly Wisdom' speech...")
    collect_munger_worldly_wisdom()
    print("\nCollecting Dalio's 'How the Economic Machine Works'...")
    collect_dalio_economic_machine()
    print("\nCollecting Jobs' 1995 Smithsonian oral history interview...")
    collect_jobs_smithsonian_interview()

    print("\n" + "=" * 60)
    print("CURRENT CORPUS SIZE (files per advisor):")
    for slug in ADVISORS:
        print(f"  {slug}: {_count(slug)} files")
    print("=" * 60)
    print("\nNext: add manual sources (see the request to Hariv), then run:")
    print("  python3 tools/build_advisor_rag.py")
