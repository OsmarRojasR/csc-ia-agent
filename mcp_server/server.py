# mcp_server.py
import uuid
from datetime import date
from typing import List, Dict
import os
from uuid import UUID

import psycopg2, psycopg2.extras
from pgvector.psycopg2 import register_vector
from dotenv import load_dotenv, find_dotenv
from fastmcp import FastMCP
from pydantic import BaseModel

from tools.embed_client import embed_texts  # tu implementación

# Carga .env aun si cambia el cwd
load_dotenv(find_dotenv())

DB_DSN = os.getenv("DB_DSN")
if not DB_DSN:
    raise RuntimeError("DB_DSN no definido. Ej: DB_DSN=postgresql://user:pass@host:5432/db")

TOPK = int(os.getenv("RAG_TOPK", "5"))

mcp = FastMCP("insurance-mcp")

def db():
    conn = psycopg2.connect(DB_DSN)
    register_vector(conn)
    return conn

# --------- Modelos estrictos para esquemas de tools (evita anyOf) ---------

class CoverageItem(BaseModel):
    name: str
    limit_amount: float = 0.0
    deductible: float = 0.0

# ======================== TOOLS: BD de seguros ============================

@mcp.tool()
def create_policy(
    customer_id: str,
    product_code: str,
    start_date: str,                 # 'YYYY-MM-DD'
    term_months: int = 12,
    premium_monthly: float = 0.0,
    status: str = "pending",         # pendiente por defecto
    activate: bool = False,          # si True, activa tras crear
    coverages: List[CoverageItem] = [],
) -> dict:
    _ = UUID(customer_id)
    if status not in ("active", "pending", "lapsed"):
        raise ValueError("status inválido")

    y, m, d = [int(x) for x in start_date.split("-")]
    _ = date(y, m, d)

    covs = [c.model_dump() if isinstance(c, CoverageItem) else c for c in coverages]
    for c in covs:
        if not c.get("name"):
            raise ValueError("coverage.name requerido")
        c["limit_amount"] = float(c.get("limit_amount", 0) or 0)
        c["deductible"]   = float(c.get("deductible", 0) or 0)

    with db() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        # idempotencia
        cur.execute("""
          SELECT id, customer_id, product_code, status, start_date, end_date, premium_monthly
          FROM policy
          WHERE customer_id=%s AND product_code=%s AND start_date=%s
          ORDER BY id LIMIT 1
        """, (customer_id, product_code, start_date))
        dup = cur.fetchone()
        if dup:
            return {"policy": dup, "coverages": [], "duplicate": True}

        new_id = str(uuid.uuid4())

        cur.execute("SELECT 1 FROM customer WHERE id=%s", (customer_id,))
        if not cur.fetchone():
            raise ValueError("customer_id no existe")

        cur.execute("SELECT 1 FROM product_doc WHERE product_code=%s LIMIT 1", (product_code,))
        _ = cur.fetchone()

        cur.execute(
            """
            INSERT INTO policy (id, customer_id, product_code, status, start_date, end_date, premium_monthly)
            VALUES (
              %(id)s, %(cid)s, %(prod)s, %(st)s, %(sd)s,
              (%(sd)s::date + (INTERVAL '1 month' * %(tm)s))::date,
              %(prem)s
            )
            RETURNING id, customer_id, product_code, status, start_date, end_date, premium_monthly
            """,
            {
                "id": new_id,
                "cid": customer_id,
                "prod": product_code,
                "st": status,
                "sd": start_date,
                "tm": term_months,
                "prem": premium_monthly,
            },
        )
        pol = cur.fetchone()

        out_covs: List[Dict] = []
        for c in covs:
            cur.execute(
                """
                INSERT INTO coverage (policy_id, name, limit_amount, deductible)
                VALUES (%(pid)s, %(name)s, %(lim)s, %(ded)s)
                RETURNING name, limit_amount, deductible
                """,
                {"pid": new_id, "name": c["name"], "lim": c["limit_amount"], "ded": c["deductible"]},
            )
            out_covs.append(cur.fetchone())

        if activate and pol:
            cur.execute("UPDATE policy SET status='active' WHERE id=%s RETURNING status", (pol["id"],))
            pol["status"] = cur.fetchone()["status"]

    return {"policy": pol or {}, "coverages": out_covs, "duplicate": False}

@mcp.tool()
def get_customer(customer_id: str) -> dict:
    """Obtiene un cliente por ID."""
    _ = UUID(customer_id)
    with db() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT id, name, email, phone FROM customer WHERE id=%s", (customer_id,))
        row = cur.fetchone()
    return row or {}

@mcp.tool()
def find_customer(
    full_name: str = "",
    name: str = "",
    email: str = "",
    phone: str = "",
) -> List[dict]:
    """Búsqueda flexible por nombre/email/teléfono (requiere EXTENSION unaccent)."""
    qname = (full_name or name).strip()
    with db() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        sql = [
            "SELECT id AS customer_id, name AS full_name, email, phone",
            "FROM customer",
            "WHERE 1=1",
        ]
        params: Dict[str, str] = {}
        if qname:
            sql.append("AND unaccent(name) ILIKE unaccent(%(name)s)")
            params["name"] = f"%{qname}%"
        if email:
            sql.append("AND unaccent(email) ILIKE unaccent(%(email)s)")
            params["email"] = f"%{email.strip()}%"
        if phone:
            sql.append("AND phone LIKE %(phone)s")
            params["phone"] = f"%{phone.strip()}%"
        sql.append("ORDER BY full_name ASC LIMIT 10")
        cur.execute("\n".join(sql), params)
        rows = cur.fetchall()
    return rows or []

@mcp.tool()
def list_policies(customer_id: str, status: str = "") -> List[dict]:
    """Lista pólizas de un cliente."""
    _ = UUID(customer_id)
    sql = """
      SELECT id, product_code, status, start_date, end_date, premium_monthly
      FROM policy WHERE customer_id=%(cid)s
    """
    params: Dict[str, str] = {"cid": customer_id}
    if status:
        sql += " AND status=%(s)s"
        params["s"] = status
    sql += " ORDER BY start_date DESC"
    with db() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return rows

@mcp.tool()
def get_policy(policy_id: str) -> dict:
    """Obtiene una póliza y sus coberturas."""
    _ = UUID(policy_id)
    with db() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""SELECT id, customer_id, product_code, status, start_date, end_date, premium_monthly
                       FROM policy WHERE id=%s""", (policy_id,))
        pol = cur.fetchone()
        cur.execute("""SELECT name, limit_amount, deductible
                       FROM coverage WHERE policy_id=%s""", (policy_id,))
        cov = cur.fetchall()
    return {"policy": pol or {}, "coverages": cov}

# ==================== RESOURCE + TOOL: RAG de productos ====================

@mcp.resource("rag://chunk/{chunk_id}")
def read_chunk(chunk_id: str):
    """Recurso RAG (solo lectura)."""
    _ = UUID(chunk_id)
    with db() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
          SELECT pc.id, pc.chunk_no, pc.content, d.product_code, d.version, d.source_uri
          FROM product_chunk pc
          JOIN product_doc d ON d.id = pc.doc_id
          WHERE pc.id = %s
        """, (chunk_id,))
        row = cur.fetchone()
    return row or {}

@mcp.tool()
def search_products(
    query: str,
    top_k: int = TOPK,
    min_score: float = 0.55,
    product_code: str = "",
    sum_insured: float = 0.0,
    deductible: float = 0.0,
    age: int = 0,
    risk_class: str = "",
    territory: str = "",
    add_ons: List[str] = [],
    car_model: str = "",
) -> List[dict]:
    """
    Busca en todo el corpus (vector + léxico). Si no hay match sólido, calcula prima.
    """
    q = (query or "").strip()
    q_vec = embed_texts([q])[0]

    # 1) Vector (pgvector, <=> = cos_dist; similitud = 1 - cos_dist)
    with db() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
          SELECT pc.id, d.product_code, d.version, pc.content,
                 1 - (pc.embedding <=> %s::vector) AS score
          FROM product_chunk pc
          JOIN product_doc d ON d.id = pc.doc_id
          ORDER BY pc.embedding <=> %s::vector
          LIMIT %s
        """, (q_vec, q_vec, max(top_k, 5)))
        rows = cur.fetchall()

    vec = [{
        "resource": f"rag://chunk/{r['id']}",
        "product_code": r["product_code"],
        "version": r["version"],
        "score": float(r["score"]),
        "preview": r["content"][:240],
        "source": "vector",
    } for r in rows]

    if vec and vec[0]["score"] >= min_score:
        return vec[:top_k]

    # 2) Léxico (acento-insensible)
    with db() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
          SELECT pc.id, d.product_code, d.version, pc.content
          FROM product_chunk pc
          JOIN product_doc d ON d.id = pc.doc_id
          WHERE unaccent(pc.content) ILIKE unaccent(%s)
             OR unaccent(d.product_code) ILIKE unaccent(%s)
          ORDER BY pc.chunk_no ASC
          LIMIT %s
        """, (f"%{q}%", f"%{q}%", max(top_k, 5)))
        lex = cur.fetchall()

    if lex:
        return [{
            "resource": f"rag://chunk/{r['id']}",
            "product_code": r["product_code"],
            "version": r["version"],
            "score": 0.0,
            "preview": r["content"][:240],
            "source": "lexical",
        } for r in lex[:top_k]]

    # 3) Fallback: cálculo de prima
    qp = q.lower()
    inferred_pc = (product_code or
                   ("hogar" if any(k in qp for k in ["hogar", "departamento", "casa"]) else
                    "auto"  if "auto" in qp else
                    "vida"  if "vida" in qp else
                    "salud" if "salud" in qp else
                    "hogar"))

    quote = calc_premium(
        product_code=inferred_pc,
        sum_insured=sum_insured,
        deductible=deductible,
        age=age,
        risk_class=risk_class,
        territory=territory,
        add_ons=add_ons,
        car_model=car_model,
    )

    return [{
        "type": "premium_quote",
        "reason": "no_rag_match",
        "inferred_product_code": inferred_pc,
        "quote": quote,
    }]


# ===================== TOOL: cálculo teórico de prima ======================

@mcp.tool()
def calc_premium(
    product_code: str,
    sum_insured: float = 0.0,
    deductible: float = 0.0,
    age: int = 0,
    risk_class: str = "",
    territory: str = "",
    add_ons: List[str] = [],
    car_model: str = "",
) -> dict:
    """Calcula una prima teórica. Devuelve desglose y total_premium."""
    import unicodedata
    def _norm(s):
        if not s: return ""
        s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower().strip()
        return s

    pc = _norm(product_code)
    rclass = _norm(risk_class)
    terr = _norm(territory)

    base_by_product = {
        "auto": 9000.0, "auto-basico": 7000.0, "auto-completo": 12000.0,
        "hogar": 4500.0, "vida": 3800.0, "salud": 5200.0,
    }
    base_premium = base_by_product.get(pc, 6000.0)

    lcm = 1.05
    risk_factor = {"alto": 1.25, "alto1": 1.25, "alto2": 1.15, "estandar": 1.0, "bajo": 0.9}.get(rclass, 1.0)
    terr_factor = {"cdmx": 1.15, "edomex": 1.10, "gdl": 1.08, "mty": 1.08}.get(terr, 1.0)

    age = age or 35
    age_discount = -0.03 if 30 <= age <= 45 else 0.0
    if age < 25: age_discount = 0.12
    if age > 60: age_discount = 0.06

    si = float(sum_insured) if sum_insured else 0.0
    ded = float(deductible) if deductible else 0.0
    deductible_factor = 0.9 if ded >= 20000 else 0.95 if ded >= 10000 else 1.0

    add_on_catalog = {"asistencia vial": 300.0, "auto sustituto": 450.0, "llantas": 250.0, "cristales": 280.0}
    add_ons_total = sum(add_on_catalog.get(_norm(x), 0.0) for x in (add_ons or []))

    premium = base_premium
    if si:
        premium *= min(max(si / 300000.0, 0.6), 3.0)

    premium *= lcm * risk_factor * terr_factor * deductible_factor * (1.0 + age_discount)
    premium += add_ons_total

    taxes = premium * 0.16
    broker_commission = premium * 0.10
    total = round(premium + taxes + broker_commission, 2)

    return {
        "product_code": product_code,
        "car_model": car_model or None,
        "sum_insured": si or None,
        "deductible": ded or None,
        "age": age,
        "risk_class": rclass or None,
        "territory": terr or None,
        "add_ons": add_ons or [],
        "base_premium": round(base_premium, 2),
        "lcm": lcm,
        "risk_factor": risk_factor,
        "territory_factor": terr_factor,
        "deductible_factor": deductible_factor,
        "age_discount": age_discount,
        "add_ons_total": round(add_ons_total, 2),
        "taxes": round(taxes, 2),
        "broker_commission": round(broker_commission, 2),
        "total_premium": total,
        "currency": "MXN",
    }


@mcp.tool()
def create_customer(
    name: str,
    email: str,
    phone: str = "",
    rfc: str = "",
    birth_date: str = "",  # 'YYYY-MM-DD' o ""
    address: str = "",
) -> dict:
    """Crea cliente; idempotente por email/phone."""
    nm = (name or "").strip()
    em = (email or "").strip().lower()
    ph = (phone or "").strip()
    if not nm or "@" not in em:
        raise ValueError("name y email válidos son obligatorios")

    bd = None
    if birth_date:
        y, m, d = [int(x) for x in birth_date.split("-")]
        from datetime import date
        bd = date(y, m, d)

    with db() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
          SELECT id, name, email, phone
          FROM customer
          WHERE lower(email)=lower(%s) OR (%s <> '' AND phone=%s)
          LIMIT 1
        """, (em, ph, ph))
        existing = cur.fetchone()
        if existing:
            return {"customer": existing, "duplicate": True}

        new_id = str(uuid.uuid4())
        cur.execute("""
          INSERT INTO customer (id, name, email, phone)
          VALUES (%s, %s, %s, %s)
          RETURNING id, name, email, phone
        """, (new_id, nm, em, ph))
        cust = cur.fetchone()

        # perfil opcional si quieres guardarlo aparte
        if any([rfc, bd, address]):
            cur.execute("""
              CREATE TABLE IF NOT EXISTS customer_profile(
                customer_id uuid PRIMARY KEY REFERENCES customer(id) ON DELETE CASCADE,
                rfc text, birth_date date, address text
              )
            """)
            cur.execute("""
              INSERT INTO customer_profile (customer_id, rfc, birth_date, address)
              VALUES (%s, %s, %s, %s)
            """, (cust["id"], rfc or None, bd, address or None))

    return {"customer": cust, "duplicate": False}

if __name__ == "__main__":
    # FastMCP via STDIO. Compatible con ADK MCPToolset.
    mcp.run()
