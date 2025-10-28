import os, uuid, glob, hashlib, logging
from urllib.parse import quote_plus
from datetime import datetime, UTC
from dotenv import load_dotenv, find_dotenv
import psycopg2
from psycopg2 import connect as pg_connect
from psycopg2.extras import RealDictCursor
from pgvector.psycopg2 import register_vector
from pypdf import PdfReader
from embed_client import embed_texts
from typing import Optional

# Cargar variables de entorno desde .env
load_dotenv(find_dotenv())

# Logging configurable
_lvl = os.getenv("HELP_INGEST_LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, _lvl, logging.INFO), format="%(levelname)s:%(message)s")
logger = logging.getLogger("help_ingest")

HELP_DB_SCHEMA = os.getenv("HELP_DB_SCHEMA", "public").strip() or "public"
# Resolución robusta del glob de entrada: por defecto relativo a este archivo (tools/docs/help/*.pdf)
_env_glob = os.getenv("HELP_DOCS_GLOB")
if _env_glob and _env_glob.strip():
    INPUT_GLOB = _env_glob.strip()
else:
    INPUT_GLOB = os.path.join(os.path.dirname(__file__), "docs", "help", "*.pdf")


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

def _redact_dsn(dsn: str) -> str:
    try:
        # No imprimimos el password
        import re
        return re.sub(r":([^:@/]+)@", ":***@", dsn)
    except Exception:
        return "(redacted)"


def ensure_schema_and_tables(conn):
        schema = HELP_DB_SCHEMA
        with conn.cursor() as cur:
                # Si el esquema es 'public', no lo crees (evita errores de permiso)
                if schema.lower() != "public":
                        logger.info("Asegurando schema '%s'", schema)
                        cur.execute("CREATE SCHEMA IF NOT EXISTS %s" % schema)
                try:
                        logger.info("Asegurando extension 'vector'")
                        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
                except Exception:
                        logger.warning("No se pudo crear extension 'vector' (posible falta de permisos); continuando…")

                logger.info("Asegurando tablas %s.help_doc y %s.help_chunk", schema, schema)
                cur.execute(
                        f"""
                        CREATE TABLE IF NOT EXISTS {schema}.help_doc (
                            id uuid PRIMARY KEY,
                            title text NOT NULL,
                            country text DEFAULT 'MX',
                            source_uri text,
                            checksum text,
                            created_at timestamptz DEFAULT now()
                        )
                        """
                )
                cur.execute(
                        f"""
                        CREATE TABLE IF NOT EXISTS {schema}.help_chunk (
                            id uuid PRIMARY KEY,
                            doc_id uuid REFERENCES {schema}.help_doc(id) ON DELETE CASCADE,
                            chunk_no int NOT NULL,
                            content text NOT NULL,
                            embedding vector(768)
                        )
                        """
                )
                conn.commit()


def read_pdf_text(path: str) -> str:
    rd = PdfReader(path)
    text = "\n".join(p.extract_text() or "" for p in rd.pages)
    # Sanitizar texto: eliminar codepoints 'surrogate' que pueden aparecer por errores
    # en la extracción y que fallan al codificar a UTF-8 en operaciones posteriores.
    def _remove_surrogates(s: str) -> str:
        if not s:
            return ""
        return ''.join(ch for ch in s if not (0xD800 <= ord(ch) <= 0xDFFF))

    text = _remove_surrogates(text)
    logger.info("Leído PDF '%s' con %d páginas y %d caracteres (sanitizado)", path, len(rd.pages), len(text))
    return text


def chunk_text(text: str, words=220, overlap=40):
    toks = text.split()
    out, i = [], 0
    while i < len(toks):
        out.append(" ".join(toks[i:i+words]))
        i += max(1, words - overlap)
    return [c for c in out if c.strip()]


def sha1(s: str) -> str:
    import hashlib
    # Asegurar que no haya surrogates que rompan la codificación a bytes
    if s is None:
        s = ""
    s_clean = ''.join(ch for ch in s if not (0xD800 <= ord(ch) <= 0xDFFF))
    return hashlib.sha1(s_clean.encode("utf-8", errors="ignore")).hexdigest()


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
    row = cur.fetchone()
    logger.info("Insertado help_doc id=%s título='%s' país=%s", row["id"] if isinstance(row, dict) else doc_id, title, country)
    return doc_id


def main(input_glob: Optional[str] = None, country: Optional[str] = None):
    # Permitir override del patrón; si no se pasa, usar INPUT_GLOB ya resuelto
    resolved_glob = (input_glob or INPUT_GLOB)
    logger.info("Conectando a DB: %s", _redact_dsn(DB_DSN))
    conn = pg_connect(DB_DSN)
    # Habilita autocommit mientras se asegura el schema/DDL y lecturas de sesión,
    # para evitar transacciones implícitas antes de cambiar autocommit.
    conn.autocommit = True
    # Registra tipos vector después de habilitar autocommit para evitar abrir transacción previa
    register_vector(conn)
    ensure_schema_and_tables(conn)
    # Contexto de sesión
    with conn.cursor() as cur:
        cur.execute("select current_user, current_database(), current_schema()")
        row = cur.fetchone()
        if row:
            u, dbn, sch = row
            logger.info("Sesion DB: user=%s db=%s schema=%s (HELP_DB_SCHEMA=%s)", u, dbn, sch, HELP_DB_SCHEMA)
        else:
            logger.info("Sesion DB: (sin datos) HELP_DB_SCHEMA=%s", HELP_DB_SCHEMA)
    # Ahora sí, transaccional para los inserts masivos
    conn.autocommit = False

    files = sorted(glob.glob(resolved_glob))
    if not files:
        logger.warning("No se encontraron PDFs con patrón: %s", resolved_glob)
    else:
        logger.info("%d PDFs encontrados (glob=%s)", len(files), resolved_glob)
    try:
        with conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                for path in files:
                    base = os.path.basename(path)
                    title = os.path.splitext(base)[0].replace("_", " ")
                    cc = (country or os.getenv("HELP_COUNTRY", "MX")).strip() or "MX"
                    text = read_pdf_text(path)
                    try:
                        doc_id = upsert_doc(cur, title, cc, path, text)
                    except Exception as e:
                        msg = getattr(e, "pgerror", str(e))
                        logger.error("Fallo INSERT en help_doc para '%s': %s", title, msg)
                        try:
                            import psycopg2
                            if isinstance(e, psycopg2.Error) and getattr(e, "diag", None):
                                logger.error("diag: %s", getattr(e.diag, "message_primary", ""))
                        except Exception:
                            pass
                        raise

                    chunks = chunk_text(text, words=220, overlap=40)
                    logger.info("Chunking '%s': %d chunks", title, len(chunks))
                    if not chunks:
                        logger.warning("Sin texto/chunks para '%s' (omitido)", title)
                        continue
                    try:
                        vecs = embed_texts(chunks)  # 768-dim
                    except Exception as e:
                        logger.error("Fallo embeddings para '%s': %s", title, e)
                        raise
                    if len(vecs) != len(chunks):
                        logger.warning("Embeddings devueltos %d != chunks %d", len(vecs), len(chunks))

                    inserted = 0
                    for idx, (content, vec) in enumerate(zip(chunks, vecs)):
                        try:
                            cid = str(uuid.uuid4())
                            cur.execute(
                                f"""
                                INSERT INTO {HELP_DB_SCHEMA}.help_chunk (id, doc_id, chunk_no, content, embedding)
                                VALUES (%s,%s,%s,%s,%s)
                                RETURNING id
                                """,
                                (cid, doc_id, idx, content, vec),
                            )
                            _ = cur.fetchone()
                            inserted += 1
                            if inserted % 50 == 0:
                                logger.info("Insertados %d chunks…", inserted)
                        except Exception as e:
                            msg = getattr(e, "pgerror", str(e))
                            logger.error("Fallo INSERT help_chunk idx=%d: %s", idx, msg)
                            try:
                                import psycopg2
                                if isinstance(e, psycopg2.Error) and getattr(e, "diag", None):
                                    logger.error("diag: %s", getattr(e.diag, "message_primary", ""))
                            except Exception:
                                pass
                            raise
                    logger.info("Insert de chunks completado para '%s': %d filas", title, inserted)
        logger.info("COMMIT exitoso")
    except Exception:
        logger.exception("Error durante ingest; ROLLBACK")
        conn.rollback()
        raise
    finally:
        conn.close()
        logger.info("Conexión cerrada")


if __name__ == "__main__":
    main()
