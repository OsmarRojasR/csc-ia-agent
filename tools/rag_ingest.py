import os, uuid, glob, hashlib
import psycopg2, psycopg2.extras
from pgvector.psycopg2 import register_vector
from datetime import datetime, UTC
from pypdf import PdfReader
from embed_client import embed_texts

DB_DSN = "postgres://admin-csc-user:admin123!@192.168.1.33:5432/postgres"

def read_pdf_text(path: str) -> str:
    rd = PdfReader(path)
    return "\n".join(p.extract_text() or "" for p in rd.pages)

def chunk_text(text: str, words=250, overlap=30):
    toks, out, i = text.split(), [], 0
    while i < len(toks):
        out.append(" ".join(toks[i:i+words]))
        i += max(1, words - overlap)
    return [c for c in out if c.strip()]

def sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()

def upsert_doc(cur, product_code: str, version: str, source_uri: str, content: str):
    doc_id = uuid.uuid4()
    cur.execute("""
      INSERT INTO product_doc (id, product_code, version, source_uri, checksum, created_at)
      VALUES (%s,%s,%s,%s,%s,%s)
    """, (str(doc_id), product_code, version, source_uri, sha1(content), datetime.now(UTC)))
    return doc_id

def main(input_glob="docs/products/*.pdf"):
    conn = psycopg2.connect(DB_DSN)
    conn.autocommit = False
    register_vector(conn)
    try:
        with conn, conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            for path in glob.glob(input_glob):
                base = os.path.basename(path)
                product_code = base.split("_")[0]
                version = base.split("_")[-1].replace(".pdf", "")
                text = read_pdf_text(path)
                doc_id = upsert_doc(cur, product_code, version, path, text)

                chunks = chunk_text(text, words=250, overlap=30)
                vecs = embed_texts(chunks)  # 768d

                for idx, (content, vec) in enumerate(zip(chunks, vecs)):
                    cur.execute("""
                      INSERT INTO product_chunk (id, doc_id, chunk_no, content, embedding)
                      VALUES (%s,%s,%s,%s,%s)
                    """, (str(uuid.uuid4()), str(doc_id), idx, content, vec))
        conn.commit()
    finally:
        conn.close()

if __name__ == "__main__":
    main()
