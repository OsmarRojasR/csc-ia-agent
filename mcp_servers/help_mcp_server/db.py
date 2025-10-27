import os
from urllib.parse import quote_plus
import psycopg2
from pgvector.psycopg2 import register_vector
from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv())

def build_db_dsn() -> str:
    # Preferir una base específica para HELP
    dsn = os.getenv("HELP_DB_DSN", "").strip() or os.getenv("DB_DSN", "").strip()
    if dsn:
        return dsn
    host = (os.getenv("HELP_PG_HOST") or os.getenv("PG_HOST", "localhost")).strip()
    port = (os.getenv("HELP_PG_PORT") or os.getenv("PG_PORT", "5432")).strip()
    user = quote_plus((os.getenv("HELP_PG_USER") or os.getenv("PG_USER", "postgres")).strip())
    pwd  = quote_plus((os.getenv("HELP_PG_PASSWORD") or os.getenv("PG_PASSWORD", "")).strip())
    # Por defecto, usa una BD llamada 'help' para separar del resto
    db   = (os.getenv("HELP_PG_DATABASE") or os.getenv("PG_DATABASE", "help")).strip()
    return f"postgresql://{user}:{pwd}@{host}:{port}/{db}"

DB_DSN = build_db_dsn()
HELP_DB_SCHEMA = os.getenv("HELP_DB_SCHEMA", "public").strip() or "public"

def db():
        """Devuelve conexión con search_path al esquema de ayuda."""
        conn = psycopg2.connect(DB_DSN)
        register_vector(conn)
        with conn.cursor() as cur:
            # Asegura que consultamos primero en el esquema deseado y siempre en public
            cur.execute("SET search_path TO %s, public", (HELP_DB_SCHEMA,))
        return conn

def ensure_schema_and_tables():
        """Crea schema y tablas si no existen (requiere permisos)."""
        schema = HELP_DB_SCHEMA
        with psycopg2.connect(DB_DSN) as conn, conn.cursor() as cur:
            # Si el esquema es 'public', no intentes crearlo (evita permisos)
            if schema.lower() != "public":
                cur.execute("CREATE SCHEMA IF NOT EXISTS %s" % schema)
                # Extension vector (si permisos); si falla, continúa y asume creada
                try:
                        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
                except Exception:
                        pass
                # Tablas: help_doc y help_chunk
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

import os
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()

def get_connection():
    return psycopg2.connect(
        host=os.getenv("PG_HOST"),
        port=os.getenv("PG_PORT"),
        user=os.getenv("PG_USER"),
        password=os.getenv("PG_PASSWORD"),
        dbname=os.getenv("PG_DATABASE"),
        cursor_factory=RealDictCursor
    )

def query(sql, params=None):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            return cur.fetchall()
