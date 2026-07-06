"""
Runtime retrieval from advisor knowledge bases. Imported by board.py.
Fails soft: if chromadb / the DB / the collection is missing, returns [].

Requires on the server:  pip install chromadb fastembed
and a chroma_db/ directory synced from the MacBook (see build_advisor_rag.py).

Uses fastembed (ONNX runtime) instead of sentence-transformers/PyTorch — a ~100MB
dependency instead of ~1.5GB, safe on small VMs. Must match the model used to build
the DB (see EMBED_MODEL in build_advisor_rag.py) or retrieval quality breaks silently.
"""

from pathlib import Path

DB_DIR = Path(__file__).parent.parent / "chroma_db"
EMBED_MODEL = "BAAI/bge-base-en-v1.5"

NAME_TO_SLUG = {
    "Elon Musk": "elon_musk", "Jeff Bezos": "jeff_bezos",
    "Warren Buffett": "warren_buffett", "Steve Jobs": "steve_jobs",
    "Charlie Munger": "charlie_munger", "Peter Thiel": "peter_thiel",
    "Ray Dalio": "ray_dalio",
}

_client = None
_model = None


def _get_client():
    global _client
    if _client is None:
        import chromadb
        _client = chromadb.PersistentClient(path=str(DB_DIR))
    return _client


def _get_model():
    global _model
    if _model is None:
        from fastembed import TextEmbedding
        _model = TextEmbedding(model_name=EMBED_MODEL)
    return _model


def retrieve_for_advisor(advisor_name, question, top_k=8):
    """Return [{text, source, year}] of the advisor's own words relevant to the question."""
    slug = NAME_TO_SLUG.get(advisor_name)
    if not slug or not DB_DIR.exists():
        return []
    try:
        coll = _get_client().get_collection(f"advisor_{slug}")
        if not coll.count():
            return []
        embedding = list(_get_model().embed([question]))[0].tolist()
        res = coll.query(query_embeddings=[embedding], n_results=min(top_k, coll.count()))
        out = []
        for doc, meta in zip(res["documents"][0], res["metadatas"][0]):
            out.append({
                "text": doc,
                "source": meta.get("source", ""),
                "year": meta.get("year", ""),
            })
        return out
    except Exception:
        return []
