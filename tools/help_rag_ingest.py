import os, uuid, glob, hashlib
from urllib.parse import quote_plus
from datetime import datetime, UTC
from dotenv import load_dotenv, find_dotenv
import psycopg2, psycopg2.extras
from pgvector.psycopg2 import register_vector
from pypdf import PdfReader
from embed_client import embed_texts
from typing import Optional

# Cargar variables de entorno desde .env
load_dotenv(find_dotenv())

HELP_DB_SCHEMA = os.getenv("HELP_DB_SCHEMA", "help").strip() or "help"
INPUT_GLOB = os.getenv("HELP_DOCS_GLOB", "tools/docs/help/*.pdf")


def _build_db_dsn() -> str:
    # Preferir una base específica para HELP
    dsn = os.getenv("HELP_DB_DSN", "").strip() or os.getenv("DB_DSN", "").strip()
    if dsn:
        return dsn
    host = (os.getenv("HELP_PG_HOST") or os.getenv("PG_HOST", "localhost")).strip()
    port = (os.getenv("HELP_PG_PORT") or os.getenv("PG_PORT", "5432")).strip()
    user = (os.getenv("HELP_PG_USER") or os.getenv("PG_USER", "postgres")).strip()
    pwd  = (os.getenv("HELP_PG_PASSWORD") or os.getenv("PG_PASSWORD", "")).strip()
    # Por defecto, usa una BD llamada 'help' para separar del resto
    db   = (os.getenv("HELP_PG_DATABASE") or os.getenv("PG_DATABASE", "help")).strip()
    return f"postgresql://{quote_plus(user)}:{quote_plus(pwd)}@{host}:{port}/{db}"

DB_DSN = _build_db_dsn()


def ensure_schema_and_tables(conn):
    schema = HELP_DB_SCHEMA
    with conn.cursor() as cur:
        cur.execute("CREATE SCHEMA IF NOT EXISTS %s" % schema)
        try:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        except Exception:
            pass
        cur.execute(f"""
          CREATE TABLE IF NOT EXISTS {schema}.help_doc (
            id uuid PRIMARY KEY,
            title text NOT NULL,
            country text DEFAULT 'MX',
            source_uri text,
            checksum text,
            created_at timestamptz DEFAULT now()
          )
        """)
        cur.execute(f"""
          CREATE TABLE IF NOT EXISTS {schema}.help_chunk (
            id uuid PRIMARY KEY,
            doc_id uuid REFERENCES {schema}.help_doc(id) ON DELETE CASCADE,
            chunk_no int NOT NULL,
            content text NOT NULL,
            embedding vector(768)
          )
        """)
        conn.commit()


def read_pdf_text(path: str) -> str:
    rd = PdfReader(path)
    return "\n".join(p.extract_text() or "" for p in rd.pages)


def chunk_text(text: str, words=220, overlap=40):
    toks = text.split()
    out, i = [], 0
    while i < len(toks):
        out.append(" ".join(toks[i:i+words]))
        i += max(1, words - overlap)
    return [c for c in out if c.strip()]


def sha1(s: str) -> str:
    import hashlib
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


def upsert_doc(cur, title: str, country: str, source_uri: str, content: str):
    doc_id = str(uuid.uuid4())
    cur.execute(
        f"""
        INSERT INTO {HELP_DB_SCHEMA}.help_doc (id, title, country, source_uri, checksum, created_at)
        VALUES (%s,%s,%s,%s,%s,%s)
        RETURNING id
        """,
        (doc_id, title, country, source_uri, sha1(content), datetime.now(UTC)),
    )
    return doc_id


def main(input_glob: str = INPUT_GLOB, country: Optional[str] = None):
    conn = psycopg2.connect(DB_DSN)
    register_vector(conn)
    ensure_schema_and_tables(conn)
    conn.autocommit = False

    try:
        with conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            for path in glob.glob(input_glob):
                base = os.path.basename(path)
                title = os.path.splitext(base)[0].replace("_", " ")
                cc = (country or os.getenv("HELP_COUNTRY", "MX")).strip() or "MX"
                text = read_pdf_text(path)
                doc_id = upsert_doc(cur, title, cc, path, text)

                chunks = chunk_text(text, words=220, overlap=40)
                if not chunks:
                    continue
                vecs = embed_texts(chunks)  # 768-dim

                for idx, (content, vec) in enumerate(zip(chunks, vecs)):
                    cur.execute(
                        f"""
                        INSERT INTO {HELP_DB_SCHEMA}.help_chunk (id, doc_id, chunk_no, content, embedding)
                        VALUES (%s,%s,%s,%s,%s)
                        """,
                        (str(uuid.uuid4()), doc_id, idx, content, vec),
                    )
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
