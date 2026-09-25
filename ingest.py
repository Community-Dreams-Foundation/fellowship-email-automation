#!/usr/bin/env python3
"""
PDF ingest: extract text → ./out/*.txt, then index chunks.

Backends:
  - pinecone + Gemini embeddings (when GEMINI_API_KEY and PINECONE_KEY are set, or --backend pinecone)
  - chromadb local (when --backend chroma, or auto when Pinecone keys missing)

Config (environment variables — use a .env file; never commit secrets):
  GEMINI_API_KEY or GOOGLE_API_KEY
  PINECONE_KEY or PINECONE_API_KEY
  INDEX_NAME            (default: cdf-fellowship-policy-index)
  PINECONE_HOST         (optional; defaults to your index host from the Pinecone dashboard)
  PDF_FOLDER            (default: .  → folder containing ingest.py)
  GEMINI_EMBEDDING_MODEL (default: gemini-embedding-001)
  GEMINI_EMBEDDING_DIM   (default: 1536 — must match Pinecone index dimension)

pypdf "Ignoring wrong pointing object" lines are harmless PDF warnings (suppressed below).
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import time
from pathlib import Path

try:
    from dotenv import load_dotenv

    # Project .env should win over inherited shell vars (e.g. empty PINECONE_KEY blocks loading).
    load_dotenv(Path(__file__).resolve().parent / ".env", override=True)
except ImportError:
    pass

EMBEDDING_MODEL = os.environ.get("GEMINI_EMBEDDING_MODEL", "gemini-embedding-001")
EMBEDDING_DIM = int(os.environ.get("GEMINI_EMBEDDING_DIM", "1536"))
PINECONE_HOST = (os.environ.get("PINECONE_HOST") or "").strip()


def _silence_pypdf_noise() -> None:
    logging.getLogger("pypdf").setLevel(logging.ERROR)


def _gemini_key() -> str | None:
    v = (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip()
    return v or None


def _pinecone_key() -> str | None:
    v = (os.environ.get("PINECONE_KEY") or os.environ.get("PINECONE_API_KEY") or "").strip()
    return v or None


def _index_name() -> str:
    return os.environ.get("INDEX_NAME", "cdf-fellowship-policy-index").strip()


def _pdf_folder(script_parent: Path) -> Path:
    raw = (os.environ.get("PDF_FOLDER") or ".").strip()
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = (script_parent / p).resolve()
    return p


def _extract_docx(docx_path: Path) -> str:
    """Extract text from a .docx file. Includes paragraphs and table cells."""
    try:
        from docx import Document
    except ImportError:
        print("Install dependency: pip install python-docx", file=sys.stderr)
        raise SystemExit(1)

    doc = Document(str(docx_path))
    parts: list[str] = []
    for para in doc.paragraphs:
        text = (para.text or "").strip()
        if text:
            parts.append(text)
    for table in doc.tables:
        for row in table.rows:
            cells = [(c.text or "").strip() for c in row.cells]
            cells = [c for c in cells if c]
            if cells:
                parts.append(" | ".join(cells))
    return "\n\n".join(parts).strip()


def extract_pdfs(pdf_dir: Path, out_txt: Path) -> list[Path]:
    """Extract text from PDFs AND DOCX files in pdf_dir → out_txt/*.txt."""
    try:
        from pypdf import PdfReader
    except ImportError:
        print("Install dependency: pip install pypdf", file=sys.stderr)
        raise SystemExit(1)

    _silence_pypdf_noise()
    pdfs = sorted(pdf_dir.glob("*.pdf"))
    docxs = sorted(p for p in pdf_dir.glob("*.docx") if not p.name.startswith("~$"))

    if not pdfs and not docxs:
        print(f"No PDF or DOCX files found in {pdf_dir}", file=sys.stderr)
        raise SystemExit(1)

    out_txt.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    for pdf in pdfs:
        reader = PdfReader(str(pdf))
        parts: list[str] = []
        for i, page in enumerate(reader.pages):
            text = page.extract_text() or ""
            parts.append(f"\n\n--- page {i + 1} ---\n\n{text}")
        body = "".join(parts).strip()
        target = out_txt / (pdf.stem + ".txt")
        target.write_text(body, encoding="utf-8")
        print(f"Wrote {target} ({len(body)} chars) [pdf]")
        written.append(target)

    for docx in docxs:
        body = _extract_docx(docx)
        target = out_txt / (docx.stem + ".txt")
        target.write_text(body, encoding="utf-8")
        print(f"Wrote {target} ({len(body)} chars) [docx]")
        written.append(target)

    return written


def chunk_text(
    text: str,
    *,
    max_chars: int = 1200,
    overlap: int = 150,
) -> list[str]:
    text = re.sub(r"\n{3,}", "\n\n", text.strip())
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    buf = ""
    for p in paras:
        if len(buf) + len(p) + 2 <= max_chars:
            buf = f"{buf}\n\n{p}".strip() if buf else p
            continue
        if buf:
            chunks.append(buf)
        if len(p) <= max_chars:
            buf = p
        else:
            for start in range(0, len(p), max_chars - overlap):
                chunks.append(p[start : start + max_chars])
            buf = ""
    if buf:
        chunks.append(buf)
    return chunks


def _collect_chunks(out_txt: Path) -> tuple[list[str], list[str], list[dict]]:
    txt_files = sorted(out_txt.glob("*.txt"))
    if not txt_files:
        print("No .txt files in out/; run without --skip-extract first.", file=sys.stderr)
        raise SystemExit(1)

    ids: list[str] = []
    documents: list[str] = []
    metadatas: list[dict] = []
    for path in txt_files:
        stem = path.stem
        raw = path.read_text(encoding="utf-8")
        for i, chunk in enumerate(chunk_text(raw)):
            cid = f"{stem}_chunk_{i:04d}"
            ids.append(cid)
            documents.append(chunk)
            metadatas.append({"source_file": stem + ".txt", "pdf_stem": stem, "chunk_index": i})
    return ids, documents, metadatas


def build_vector_store_chroma(out_txt: Path, chroma_path: Path) -> None:
    import chromadb

    out_txt.mkdir(parents=True, exist_ok=True)
    ids, documents, metadatas = _collect_chunks(out_txt)

    chroma_path.parent.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(chroma_path))
    try:
        client.delete_collection("fellowship_pdfs")
    except Exception:
        pass
    collection = client.get_or_create_collection(
        name="fellowship_pdfs",
        metadata={"description": "Fellowship knowledge-base documents"},
    )

    batch = 64
    for start in range(0, len(documents), batch):
        end = start + batch
        collection.add(
            ids=ids[start:end],
            documents=documents[start:end],
            metadatas=metadatas[start:end],
        )

    print(f"Vector store (Chroma): {chroma_path} ({len(documents)} chunks)")


def _gemini_embed_batch(client, texts: list[str], task_type: str = "RETRIEVAL_DOCUMENT") -> list[list[float]]:
    from google.genai import types

    r = client.models.embed_content(
        model=EMBEDDING_MODEL,
        contents=texts,
        config=types.EmbedContentConfig(
            output_dimensionality=EMBEDDING_DIM,
            task_type=task_type,
        ),
    )
    return [list(e.values) for e in r.embeddings]


def build_vector_store_pinecone(out_txt: Path, index_name: str) -> None:
    from google import genai
    from pinecone import Pinecone

    gkey = _gemini_key()
    pkey = _pinecone_key()
    if not gkey or not pkey:
        print("Pinecone backend requires GEMINI_API_KEY and PINECONE_KEY in the environment.", file=sys.stderr)
        raise SystemExit(1)

    ids, documents, metadatas = _collect_chunks(out_txt)
    gem = genai.Client(api_key=gkey)
    pc = Pinecone(api_key=pkey)

    try:
        desc = pc.describe_index(index_name)
        dim = desc.dimension
        if dim is not None and int(dim) != EMBEDDING_DIM:
            print(
                f"Pinecone index {index_name!r} has dimension {dim}; "
                f"{EMBEDDING_MODEL} uses {EMBEDDING_DIM}. Create or pick a matching index.",
                file=sys.stderr,
            )
            raise SystemExit(1)
    except SystemExit:
        raise
    except Exception as e:
        print(
            f"Cannot use Pinecone index {index_name!r}: {e}\n"
            f"Create a serverless (or pod) index with dimension {EMBEDDING_DIM} for {EMBEDDING_MODEL}.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    index = pc.Index(index_name, host=PINECONE_HOST)
    try:
        # Fresh indexes can return 404 "Namespace not found" for delete_all.
        # This is safe to ignore before first upsert.
        index.delete(delete_all=True)
    except Exception as e:
        if "Namespace not found" not in str(e):
            raise

    # Gemini embeddings: batch size is conservative to stay under request limits.
    batch_size = 16
    for start in range(0, len(documents), batch_size):
        end = min(start + batch_size, len(documents))
        batch_docs = documents[start:end]
        batch_ids = ids[start:end]
        batch_meta = metadatas[start:end]
        vectors = _gemini_embed_batch(gem, batch_docs, task_type="RETRIEVAL_DOCUMENT")
        upsert = [
            {"id": bid, "values": vec, "metadata": {**meta, "text": doc[:39000]}}
            for bid, vec, meta, doc in zip(batch_ids, vectors, batch_meta, batch_docs)
        ]
        index.upsert(vectors=upsert)
        time.sleep(0.1)

    print(f"Vector store (Pinecone): index {index_name!r} ({len(documents)} vectors)")


def query_store_chroma(chroma_path: Path, question: str, k: int) -> None:
    import chromadb

    client = chromadb.PersistentClient(path=str(chroma_path))
    collection = client.get_collection("fellowship_pdfs")
    res = collection.query(query_texts=[question], n_results=k)
    print(f"Query: {question!r}\n")
    for i, (doc, meta, dist) in enumerate(
        zip(res["documents"][0], res["metadatas"][0], res["distances"][0]),
        start=1,
    ):
        src = meta.get("source_file", "?")
        print(f"--- hit {i} ({src}, distance={dist:.4f}) ---")
        preview = doc[:500] + ("…" if len(doc) > 500 else "")
        print(preview)
        print()


def query_store_pinecone(index_name: str, question: str, k: int) -> None:
    from google import genai
    from pinecone import Pinecone

    gkey = _gemini_key()
    pkey = _pinecone_key()
    if not gkey or not pkey:
        print("Query requires GEMINI_API_KEY and PINECONE_KEY.", file=sys.stderr)
        raise SystemExit(1)

    gem = genai.Client(api_key=gkey)
    pc = Pinecone(api_key=pkey)
    index = pc.Index(index_name, host=PINECONE_HOST)
    qv = _gemini_embed_batch(gem, [question], task_type="RETRIEVAL_QUERY")[0]
    res = index.query(vector=qv, top_k=k, include_metadata=True)
    print(f"Query: {question!r}\n")
    matches = res.get("matches") or []
    for i, m in enumerate(matches, start=1):
        meta = m.metadata or {}
        src = meta.get("source_file", "?")
        score = m.score if m.score is not None else 0.0
        text = meta.get("text", "")
        print(f"--- hit {i} ({src}, score={score:.4f}) ---")
        preview = (text or "")[:500] + ("…" if len(text or "") > 500 else "")
        print(preview)
        print()


def verify_pinecone_connection(index_name: str) -> int:
    """Ping Pinecone + Gemini; print index stats. Exit 0 if OK."""
    from google import genai
    from pinecone import Pinecone

    env_path = Path(__file__).resolve().parent / ".env"
    print(f"Looking for: {env_path}")
    print(f".env file exists: {env_path.exists()}")
    has_gem = bool(_gemini_key())
    has_pc = bool(_pinecone_key())
    print(f"GEMINI_API_KEY set: {has_gem}")
    print(f"PINECONE_KEY set: {has_pc}")

    if not has_gem or not has_pc:
        print(
            "\nFix: edit .env in this folder (same directory as ingest.py).\n"
            "Required:\n"
            "  GEMINI_API_KEY=AIza...\n"
            "  PINECONE_KEY=pcsk_...\n",
            file=sys.stderr,
        )
        return 1

    pc = Pinecone(api_key=_pinecone_key())
    try:
        desc = pc.describe_index(index_name)
        print(f"Pinecone index {index_name!r}: host={desc.host!r} dimension={desc.dimension!r}")
    except Exception as e:
        print(f"Pinecone describe_index failed: {e}", file=sys.stderr)
        return 1

    index = pc.Index(index_name, host=PINECONE_HOST)
    stats = index.describe_index_stats()
    print(f"Index stats: {stats}")

    gem = genai.Client(api_key=_gemini_key())
    _gemini_embed_batch(gem, ["connection test"], task_type="RETRIEVAL_QUERY")
    print("Gemini embeddings: OK (test embedding created).")
    print("You are connected to Pinecone + Gemini.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="PDF extract + vector index (Chroma or Pinecone)")
    parser.add_argument(
        "--backend",
        choices=("auto", "chroma", "pinecone"),
        default="auto",
        help="auto: Pinecone if keys set, else Chroma",
    )
    parser.add_argument("--skip-extract", action="store_true", help="Only (re)build index from out/*.txt")
    parser.add_argument("--query", type=str, default=None, help="Semantic search (uses backend)")
    parser.add_argument("-k", type=int, default=5, help="Top-k for --query")
    parser.add_argument(
        "--verify-pinecone",
        action="store_true",
        help="Test Pinecone + OpenAI keys and print index stats (no ingest)",
    )
    args = parser.parse_args()

    script_parent = Path(__file__).resolve().parent
    pdf_dir = _pdf_folder(script_parent)
    out_txt = script_parent / "out"
    chroma_path = out_txt / "chroma_db"
    index_name = _index_name()

    if args.verify_pinecone:
        return verify_pinecone_connection(index_name)

    use_pinecone = args.backend == "pinecone" or (
        args.backend == "auto" and _gemini_key() and _pinecone_key()
    )
    if args.backend == "pinecone" and (not _gemini_key() or not _pinecone_key()):
        print("--backend pinecone requires GEMINI_API_KEY and PINECONE_KEY.", file=sys.stderr)
        return 1

    if args.query:
        if use_pinecone:
            print("Backend: Pinecone")
            query_store_pinecone(index_name, args.query, k=args.k)
        else:
            print("Backend: Chroma")
            if not chroma_path.exists():
                print(f"No Chroma index at {chroma_path}; run ingest first.", file=sys.stderr)
                return 1
            query_store_chroma(chroma_path, args.query, k=args.k)
        return 0

    if not args.skip_extract:
        extract_pdfs(pdf_dir, out_txt)

    if use_pinecone:
        print(f"Backend: Pinecone (index={index_name!r}, host from PINECONE_HOST / default)")
        build_vector_store_pinecone(out_txt, index_name)
        print(f"Done. PDFs from {pdf_dir}; text in out/*.txt; Pinecone index {index_name!r}")
    else:
        print(
            "Backend: Chroma (local out/chroma_db). "
            "To use Pinecone: add GEMINI_API_KEY and PINECONE_KEY to .env in this folder, then run again."
        )
        build_vector_store_chroma(out_txt, chroma_path)
        print(f"Done. PDFs from {pdf_dir}; text in out/*.txt; Chroma at {chroma_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
