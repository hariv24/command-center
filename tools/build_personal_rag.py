"""
Incremental personal-history RAG — a second Chroma collection (separate from
the advisor-voice RAG) indexing Hariv's OWN data: board session questions/
responses, daily logs, decisions, wellness/conviction chats. This gives
advisors and coaches a real memory of specifics ("In May you said Manikandan
promised the ERP by June 15") instead of only the curated live-context summary.

Uses fastembed (same as the advisor RAG) so it runs cheaply on the server —
no need for the Mac-side build step this corpus grows constantly, so it's
embedded incrementally in-place, server-side, after each session.

Run manually to backfill existing history:
    python3 tools/build_personal_rag.py
Called incrementally from board.py after each board session completes.
"""

import json
from pathlib import Path

ROOT = Path(__file__).parent.parent
DB_DIR = ROOT / "personal_rag_db"
DATA_DIR = ROOT / "data"
SESSIONS_DIR = ROOT / "sessions"

EMBED_MODEL = "BAAI/bge-base-en-v1.5"
COLLECTION = "personal_history"

_model = None
_client = None


def _get_model():
    global _model
    if _model is None:
        from fastembed import TextEmbedding
        _model = TextEmbedding(model_name=EMBED_MODEL)
    return _model


def _get_client():
    global _client
    if _client is None:
        import chromadb
        _client = chromadb.PersistentClient(path=str(DB_DIR))
    return _client


def _already_indexed_ids(coll):
    if not coll.count():
        return set()
    return set(coll.get(include=[])["ids"])


def _collect_chunks():
    """Yields (id, text, metadata) for every indexable piece of personal history."""
    # Board sessions — question + each advisor response + synthesis
    for f in sorted(SESSIONS_DIR.glob("*.json")):
        try:
            s = json.loads(f.read_text())
        except Exception:
            continue
        date = s.get("timestamp", "")[:10]
        for t in s.get("turns", [s]) if s.get("turns") else [s]:
            q = t.get("question", s.get("question", ""))
            for r in t.get("responses", s.get("responses", [])):
                cid = f"session_{s.get('id', f.stem)}_{t.get('turn', 1)}_{r.get('name','')}"
                text = f"On {date}, Hariv asked the board: \"{q}\"\n{r.get('name','')} responded: {r.get('response','')}"
                yield cid, text, {"type": "board", "date": date, "advisor": r.get("name", "")}
            synth = t.get("synthesis", s.get("synthesis", ""))
            if synth:
                cid = f"session_{s.get('id', f.stem)}_{t.get('turn', 1)}_synthesis"
                yield cid, f"On {date}, board synthesis for \"{q}\": {synth}", {"type": "board_synthesis", "date": date}

    # Daily logs
    log_file = DATA_DIR / "daily_log.json"
    if log_file.exists():
        for l in json.loads(log_file.read_text()):
            cid = f"log_{l.get('date','')}"
            text = f"On {l.get('date','')}, Hariv logged: Did: {l.get('did','')} | Blocked: {l.get('didnt','')} | Energy: {l.get('energy','')}"
            yield cid, text, {"type": "daily_log", "date": l.get("date", "")}

    # Decisions
    dec_file = DATA_DIR / "decisions.json"
    if dec_file.exists():
        for d in json.loads(dec_file.read_text()):
            cid = f"decision_{d.get('id','')}"
            text = f"On {d.get('date','')}, Hariv decided: {d.get('decision','')} (confidence {d.get('confidence','?')}/10)"
            if d.get("outcome"):
                text += f". Outcome: {d['outcome']}"
            yield cid, text, {"type": "decision", "date": d.get("date", "")}

    # Wellness + conviction chats
    for fname, label in [("vitals.json", "wellness"), ("conviction_chat.json", "conviction")]:
        p = DATA_DIR / fname
        if not p.exists():
            continue
        for i, entry in enumerate(json.loads(p.read_text())):
            cid = f"{label}_{entry.get('date', i)}_{i}"
            user_text = entry.get("user", "")
            if user_text:
                text = f"In a {label} conversation on {entry.get('date','')}, Hariv said: {user_text}"
                yield cid, text, {"type": label, "date": entry.get("date", "")}


def build_incremental():
    coll = _get_client().get_or_create_collection(COLLECTION)
    existing = _already_indexed_ids(coll)
    model = _get_model()

    docs, metas, ids = [], [], []
    for cid, text, meta in _collect_chunks():
        if cid in existing or not text.strip():
            continue
        docs.append(text[:2000])
        metas.append(meta)
        ids.append(cid)

    if not docs:
        return 0

    embeddings = list(model.embed(docs, batch_size=32))
    B = 200
    for i in range(0, len(docs), B):
        coll.add(
            documents=docs[i:i+B],
            embeddings=[e.tolist() for e in embeddings[i:i+B]],
            metadatas=metas[i:i+B],
            ids=ids[i:i+B],
        )
    return len(docs)


def retrieve_personal_context(question, top_k=5):
    """Returns [{text, type, date}] of Hariv's own past statements relevant to the question."""
    if not DB_DIR.exists():
        return []
    try:
        coll = _get_client().get_collection(COLLECTION)
        if not coll.count():
            return []
        embedding = list(_get_model().embed([question]))[0].tolist()
        res = coll.query(query_embeddings=[embedding], n_results=min(top_k, coll.count()))
        out = []
        for doc, meta in zip(res["documents"][0], res["metadatas"][0]):
            out.append({"text": doc, "type": meta.get("type", ""), "date": meta.get("date", "")})
        return out
    except Exception:
        return []


if __name__ == "__main__":
    n = build_incremental()
    print(f"Indexed {n} new personal-history chunks into {DB_DIR}")
