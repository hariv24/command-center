"""
Embed advisor source material into a Chroma vector DB.

Run on the MacBook (fast local embedding, no API cost):
    pip install chromadb sentence-transformers
    python3 tools/build_advisor_rag.py            # full rebuild
    python3 tools/build_advisor_rag.py --update   # only embed new files

Then push to the VM:
    rsync -avz -e "ssh -i key/ssh-key-2026-06-27.key" chroma_db/ ubuntu@140.245.237.122:~/agent/chroma_db/

Chunking: ~300 words per chunk with 40-word overlap (~400 tokens / 50 overlap).
Embedding model: sentence-transformers all-mpnet-base-v2 (free, runs on Metal).
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
SOURCES_DIR = ROOT / "rag_sources"
DB_DIR = ROOT / "chroma_db"

CHUNK_WORDS = 300
OVERLAP_WORDS = 40

SLUG_TO_NAME = {
    "elon_musk": "Elon Musk", "jeff_bezos": "Jeff Bezos",
    "warren_buffett": "Warren Buffett", "steve_jobs": "Steve Jobs",
    "charlie_munger": "Charlie Munger", "peter_thiel": "Peter Thiel",
    "ray_dalio": "Ray Dalio",
}


def _parse_doc(path):
    text = path.read_text(errors="ignore")
    source, year = path.stem, ""
    m = re.match(r"SOURCE:\s*(.+)\n(?:YEAR:\s*(\S+)\n)?", text)
    if m:
        source = m.group(1).strip()
        year = (m.group(2) or "").strip()
        text = text[m.end():].strip()
    return source, year, text


def _chunk(text):
    words = text.split()
    chunks = []
    step = CHUNK_WORDS - OVERLAP_WORDS
    for i in range(0, max(len(words) - OVERLAP_WORDS, 1), step):
        chunk = " ".join(words[i:i + CHUNK_WORDS])
        if len(chunk) > 200:
            chunks.append(chunk)
    return chunks


def build(update_only=False):
    import chromadb
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer("all-mpnet-base-v2")
    client = chromadb.PersistentClient(path=str(DB_DIR))

    for slug, name in SLUG_TO_NAME.items():
        raw_dir = SOURCES_DIR / slug / "raw"
        if not raw_dir.exists():
            continue
        coll = client.get_or_create_collection(f"advisor_{slug}")
        existing_sources = set()
        if update_only and coll.count():
            existing_sources = {m["file"] for m in coll.get(include=["metadatas"])["metadatas"]}

        docs, metas, ids = [], [], []
        for f in sorted(raw_dir.glob("*.txt")):
            if f.name in existing_sources:
                continue
            source, year, text = _parse_doc(f)
            for i, chunk in enumerate(_chunk(text)):
                docs.append(chunk)
                metas.append({"source": source, "year": year, "file": f.name})
                ids.append(f"{slug}_{f.stem}_{i}")
        if not docs:
            print(f"{name}: nothing new")
            continue
        print(f"{name}: embedding {len(docs)} chunks...")
        embeddings = model.encode(docs, show_progress_bar=True, batch_size=64)
        # Chroma add in batches
        B = 500
        for i in range(0, len(docs), B):
            coll.add(
                documents=docs[i:i+B],
                embeddings=[e.tolist() for e in embeddings[i:i+B]],
                metadatas=metas[i:i+B],
                ids=ids[i:i+B],
            )
        print(f"{name}: collection now has {coll.count()} chunks")

    print(f"\nDB written to {DB_DIR}. rsync it to the VM (see module docstring).")


if __name__ == "__main__":
    build(update_only="--update" in sys.argv)
