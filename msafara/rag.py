"""RAG layer: chunk -> embed -> store (Chroma, numpy fallback) -> retrieve.

Honest description of what this is:
  * Embedder: hashing TF-IDF over unigrams+bigrams (crc32 -> 1024 buckets,
    idf fitted on the KB). Lexical, deterministic, no model download. It is
    NOT a neural embedding model.
  * Store: Chroma EphemeralClient (in-memory, rebuilt at process start, using
    our precomputed embeddings and cosine distance). If chromadb is missing a
    numpy brute-force index with identical filter semantics is used.
  * Retrieval = metadata filter (vendor_id / doc_type / city / category / star
    ...) + semantic ranking, which is how the agents fetch rate cards and past
    quotes "by vendor ID plus semantic match".
"""
import re
import uuid
import zlib
from dataclasses import dataclass, field

import numpy as np

from . import kb

DIM = 1024


# ------------------------------------------------------------------ chunking
def chunk_text(text: str, max_chars: int = 600, overlap: int = 80) -> list[str]:
    """Sentence-aware chunker. Short texts stay whole; long ones split on sentence
    boundaries with a small character overlap carried into the next chunk."""
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_chars:
        return [text] if text else []
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks, cur = [], ""
    for s in sentences:
        if cur and len(cur) + 1 + len(s) > max_chars:
            chunks.append(cur)
            tail = cur[-overlap:] if overlap else ""
            cur = (tail + " " + s).strip() if tail else s
        else:
            cur = f"{cur} {s}".strip()
        while len(cur) > max_chars:  # single very long sentence
            chunks.append(cur[:max_chars])
            cur = cur[max_chars - overlap:]
    if cur:
        chunks.append(cur)
    return chunks


# ------------------------------------------------------------------ embedding
_TOK = re.compile(r"[a-z0-9]+")


def _terms(text: str) -> list[str]:
    toks = _TOK.findall(text.lower())
    return toks + [f"{a}_{b}" for a, b in zip(toks, toks[1:])]


class HashingEmbedder:
    def __init__(self, dim: int = DIM):
        self.dim = dim
        self.idf = np.ones(dim, dtype=np.float32)

    def _tf(self, text: str) -> np.ndarray:
        v = np.zeros(self.dim, dtype=np.float32)
        for t in _terms(text):
            v[zlib.crc32(t.encode()) % self.dim] += 1.0
        return np.log1p(v)

    def fit(self, texts: list[str]):
        df = np.zeros(self.dim, dtype=np.float32)
        for t in texts:
            df += (self._tf(t) > 0)
        n = max(len(texts), 1)
        self.idf = (np.log((1 + n) / (1 + df)) + 1.0).astype(np.float32)
        return self

    def embed(self, text: str) -> list[float]:
        v = self._tf(text) * self.idf
        n = np.linalg.norm(v)
        return (v / n if n else v).astype(np.float32).tolist()


# ------------------------------------------------------------------ filtering
def _match(meta: dict, where: dict | None) -> bool:
    """Equality, $in and $and - the subset of Chroma's where-language we use."""
    if not where:
        return True
    for k, cond in where.items():
        if k == "$and":
            if not all(_match(meta, c) for c in cond):
                return False
        elif isinstance(cond, dict) and "$in" in cond:
            if meta.get(k) not in cond["$in"]:
                return False
        else:
            if meta.get(k) != cond:
                return False
    return True


def _to_chroma_where(where: dict | None):
    if not where:
        return None
    items = [{k: v} for k, v in where.items()]
    return items[0] if len(items) == 1 else {"$and": items}


@dataclass
class Hit:
    id: str
    text: str
    metadata: dict
    score: float  # cosine similarity, higher = closer


class VectorIndex:
    def __init__(self, prefer_chroma: bool = True):
        self.embedder = HashingEmbedder()
        self.backend = "numpy"
        self._col = None
        self._ids: list[str] = []
        self._texts: list[str] = []
        self._metas: list[dict] = []
        self._mat = np.zeros((0, DIM), dtype=np.float32)
        self._prefer_chroma = prefer_chroma

    def build(self, docs: list[dict]):
        chunks = []
        for d in docs:
            for i, c in enumerate(chunk_text(d["text"])):
                chunks.append(dict(id=f"{d['id']}#{i}", text=c, metadata=d["metadata"]))
        self.embedder.fit([c["text"] for c in chunks])
        embs = [self.embedder.embed(c["text"]) for c in chunks]
        self._ids = [c["id"] for c in chunks]
        self._texts = [c["text"] for c in chunks]
        self._metas = [c["metadata"] for c in chunks]
        self._mat = np.array(embs, dtype=np.float32)
        if self._prefer_chroma:
            try:
                import chromadb
                client = chromadb.EphemeralClient()
                self._col = client.create_collection(f"msafara_{uuid.uuid4().hex[:10]}",
                                                     metadata={"hnsw:space": "cosine"})
                for s in range(0, len(chunks), 500):
                    b = chunks[s:s + 500]
                    self._col.add(ids=[c["id"] for c in b], documents=[c["text"] for c in b],
                                  embeddings=embs[s:s + 500], metadatas=[c["metadata"] for c in b])
                self.backend = "chroma"
            except Exception:
                self._col = None
                self.backend = "numpy"
        return self

    def __len__(self):
        return len(self._ids)

    def query(self, text: str, where: dict | None = None, k: int = 5) -> list[Hit]:
        qv = np.array(self.embedder.embed(text), dtype=np.float32)
        if self._col is not None:
            r = self._col.query(query_embeddings=[qv.tolist()], n_results=k, where=_to_chroma_where(where))
            return [Hit(i, d, m, 1.0 - dist) for i, d, m, dist in
                    zip(r["ids"][0], r["documents"][0], r["metadatas"][0], r["distances"][0])]
        idx = [i for i, m in enumerate(self._metas) if _match(m, where)]
        if not idx:
            return []
        sims = self._mat[idx] @ qv
        order = np.argsort(-sims, kind="stable")[:k]
        return [Hit(self._ids[idx[o]], self._texts[idx[o]], self._metas[idx[o]], float(sims[o])) for o in order]


_INDEX: VectorIndex | None = None


def get_index(rebuild: bool = False) -> VectorIndex:
    global _INDEX
    if _INDEX is None or rebuild:
        _INDEX = VectorIndex().build(kb.documents())
    return _INDEX


def retrieve(query: str, where: dict | None = None, k: int = 5) -> list[Hit]:
    return get_index().query(query, where, k)
