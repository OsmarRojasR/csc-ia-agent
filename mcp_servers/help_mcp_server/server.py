"""
MCP de apoyo a mujeres víctimas de violencia (asesoría y protocolos).
Provee herramientas para:
  - Buscar protocolos y pasos recomendados.
  - Obtener contactos de emergencia y líneas de ayuda.
  - Sugerir un plan de seguridad personalizado.

Nota: Este servidor no requiere base de datos. Usa un KB mínimo estático.
"""

from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from fastmcp import FastMCP
import os
import json
from pathlib import Path
import unicodedata
import psycopg2, psycopg2.extras
from dotenv import load_dotenv, find_dotenv
from tools.embed_client import embed_texts
from .db import db, ensure_schema_and_tables, HELP_DB_SCHEMA

mcp = FastMCP("help-womens-mcp")
load_dotenv(find_dotenv())
TOPK = int(os.getenv("RAG_TOPK", "5"))
AUTO_INIT_SCHEMA = os.getenv("HELP_AUTO_INIT_SCHEMA", "true").lower() in ("1","true","yes")
if AUTO_INIT_SCHEMA:
    try:
        ensure_schema_and_tables()
    except Exception:
        pass

# ----------------------------- KB estático ---------------------------------

@dataclass
class Protocol:
    id: str
    title: str
    country: str
    tags: List[str]
    steps: List[str]
    contacts: List[Dict[str, str]]  # {name, phone, url}


KB: List[Protocol] = [
    Protocol(
        id="emergencia-mx-001",
        title="Emergencia inmediata (México)",
        country="MX",
        tags=["emergencia", "riesgo", "violencia"],
        steps=[
            "Si estás en peligro inminente, llama al 911 de inmediato.",
            "Si no puedes hablar, intenta mantener la línea abierta o utiliza palabras clave para pedir ayuda.",
            "De ser posible, mueve a menores o dependientes a un lugar seguro dentro de tu hogar (cerca de una salida).",
            "Identifica una vecina o persona de confianza a quien puedas avisar con una palabra clave.",
        ],
        contacts=[
            {"name": "Emergencias", "phone": "911", "url": "https://www.gob.mx/911"},
            {"name": "Línea Mujeres (CDMX)", "phone": "*765", "url": "https://www.semujeres.cdmx.gob.mx/"},
        ],
    ),
    Protocol(
        id="denuncia-mx-001",
        title="Cómo denunciar y solicitar protección (México)",
        country="MX",
        tags=["denuncia", "proteccion", "orden", "compañero"],
        steps=[
            "Documenta incidentes (fechas, fotos de lesiones/daños, mensajes amenazantes).",
            "Acude a la Fiscalía/Ministerio Público o llama para orientación legal gratuita.",
            "Solicita una Orden de Protección si hay riesgo; puede incluir restricción de acercamiento.",
            "Pregunta por refugios temporales y apoyo psicológico y legal.",
        ],
        contacts=[
            {"name": "Fiscalía Local", "phone": "—", "url": "https://www.gob.mx/segob/acciones-y-programas/violencia-contra-las-mujeres"},
            {"name": "LADA sin costo", "phone": "800 911 25 11", "url": "https://inmujeres.gob.mx"},
        ],
    ),
]


def _simple_match_score(text: str, proto: Protocol) -> float:
    t = (text or "").lower()
    score = 0.0
    for token in set(t.split()):
        if token in proto.title.lower():
            score += 2.0
        if token in " ".join(proto.tags).lower():
            score += 1.0
    return score


# ------------------------------- TOOLS -------------------------------------

def _get_emergency_contacts(country: str = "MX") -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for p in KB:
        if p.country.upper() == (country or "").upper():
            out.extend(p.contacts)
    # deduplicar por (name, phone)
    seen = set()
    uniq: List[Dict[str, str]] = []
    for c in out:
        key = (c.get("name"), c.get("phone"))
        if key not in seen:
            uniq.append(c)
            seen.add(key)
    return uniq

@mcp.tool()
def get_emergency_contacts(country: str = "MX") -> List[Dict[str, str]]:
    """Devuelve contactos de emergencia y líneas de ayuda por país (ej: MX)."""
    return _get_emergency_contacts(country)


@mcp.tool()
def search_protocols(query: str, country: str = "MX", top_k: int = 3) -> List[Dict[str, Any]]:
    """Busca protocolos relevantes en el KB por texto libre y país."""
    items: List[Dict[str, Any]] = []
    for p in KB:
        if country and p.country.upper() != country.upper():
            continue
        score = _simple_match_score(query, p)
        if score > 0 or not query:
            items.append({
                "id": p.id,
                "title": p.title,
                "country": p.country,
                "score": score,
                "resource": f"kb://protocol/{p.id}",
            })
    items.sort(key=lambda x: x["score"], reverse=True)
    return items[: max(1, top_k)]


@mcp.tool()
def build_safety_plan(
    situation: str = "",
    children_present: bool = False,
    constraints: List[str] = [],
    country: str = "MX",
) -> Dict[str, Any]:
    """Genera sugerencias de plan de seguridad a partir de la situación y restricciones."""
    plan: Dict[str, Any] = {
        "immediate": [
            "Identifica una ruta de escape segura y una palabra clave con una persona de confianza.",
            "Prepara una bolsa con documentos, dinero en efectivo si es posible, llaves y medicina.",
            "Si hay riesgo inminente, llama al 911 y busca un lugar donde puedas cerrar la puerta y pedir ayuda.",
        ],
        "contacts": _get_emergency_contacts(country=country),
        "children": [],
        "notes": "Este plan no sustituye asesoría legal o psicológica profesional.",
    }
    if children_present:
        plan["children"] = [
            "Enseña a tus hijas e hijos cómo llamar al 911 y pedir ayuda, sin ponerse en riesgo.",
            "Acuerda un punto de encuentro seguro y rápido (p. ej., con una vecina).",
        ]
    if constraints:
        plan["constraints"] = constraints
    if situation:
        plan["context"] = situation
    return plan


# ------------------------------ RESOURCES ----------------------------------

@mcp.resource("kb://protocol/{proto_id}")
def read_protocol(proto_id: str) -> Dict[str, Any]:
    for p in KB:
        if p.id == proto_id:
            return {
                "id": p.id,
                "title": p.title,
                "country": p.country,
                "tags": p.tags,
                "steps": p.steps,
                "contacts": p.contacts,
            }
    return {}


# ====================== RAG en PostgreSQL (otro schema) ====================

@mcp.resource("help://chunk/{chunk_id}")
def read_help_chunk(chunk_id: str) -> Dict[str, Any]:
    with db() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(f"""
          SELECT c.id, c.chunk_no, c.content, d.title, d.country, d.source_uri
          FROM {HELP_DB_SCHEMA}.help_chunk c
          JOIN {HELP_DB_SCHEMA}.help_doc d ON d.id = c.doc_id
          WHERE c.id = %s
        """, (chunk_id,))
        row = cur.fetchone()
    return row or {}


@mcp.tool()
def search_help(
    query: str,
    country: str = "",
    top_k: int = TOPK,
    min_score: float = 0.55,
) -> List[Dict[str, Any]]:
    """Busca en KB vectorial del esquema HELP (help_doc/help_chunk)."""
    q = (query or "").strip()
    q_vec = embed_texts([q])[0]

    with db() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        base = f"""
          SELECT c.id, d.title, d.country, c.content,
                 1 - (c.embedding <=> %s::vector) AS score
          FROM {HELP_DB_SCHEMA}.help_chunk c
          JOIN {HELP_DB_SCHEMA}.help_doc d ON d.id = c.doc_id
        """
        tail = " ORDER BY c.embedding <=> %s::vector LIMIT %s"
        if country:
            sql = base + " WHERE d.country = %s" + tail
            cur.execute(sql, (q_vec, q_vec, country, max(top_k, 5)))
        else:
            sql = base + tail
            cur.execute(sql, (q_vec, q_vec, max(top_k, 5)))
        rows = cur.fetchall() or []

    vec = [{
        "resource": f"help://chunk/{r['id']}",
        "title": r["title"],
        "country": r["country"],
        "score": float(r["score"]),
        "preview": (r["content"] or "")[:240],
        "source": "vector",
    } for r in rows]

    return [x for x in vec if x["score"] >= min_score][: top_k]


# ------------------------ Datos de delitos (JSON local) --------------------
# Carga robusta: usa HELP_CRIME_DATA_FILE si se define; si no, resuelve relativo a este módulo
_BASE_DIR = Path(__file__).resolve().parent
_DEFAULT_CRIME_PATH = _BASE_DIR / "data" / "crime_data.json"
DATA_FILE = os.getenv("HELP_CRIME_DATA_FILE") or str(_DEFAULT_CRIME_PATH)
CRIME_DATA: List[Dict[str, Any]] = []
try:
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        CRIME_DATA = json.load(f) or []
except Exception:
    CRIME_DATA = []

# Utils
MONTHS_ES = [
    "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
    "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
]

def _norm(s: str) -> str:
    try:
        s = s.strip()
    except Exception:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return s.lower()

def _get_first(item: Dict[str, Any], candidates: List[str]) -> Optional[Any]:
    if not item:
        return None
    # mapa de claves normalizadas
    norm_map = { _norm(k): k for k in item.keys() }
    for c in candidates:
        k = norm_map.get(_norm(c))
        if k in item:
            return item.get(k)
    return None

def _get_month_value(item: Dict[str, Any], month: str) -> float:
    if not month:
        return 0.0
    # intenta variantes de capitalización
    keys = [month, month.title(), month.capitalize(), month.upper(), month.lower()]
    for k in keys:
        try:
            v = item.get(k)
            if v is None:
                continue
            return float(v)
        except Exception:
            continue
    # intenta buscar por normalización
    norm_month = _norm(month)
    for k, v in item.items():
        if _norm(k) == norm_month:
            try:
                return float(v)
            except Exception:
                return 0.0
    return 0.0

def _sum_months(item: Dict[str, Any]) -> float:
    total = 0.0
    for m in MONTHS_ES:
        try:
            v = item.get(m)
            if v is None:
                continue
            total += float(v)
        except Exception:
            continue
    return total

@mcp.tool()
def search_crime_data(query: str, top_k: int = 5) -> List[Dict[str, Any]]:
    """Búsqueda simple de texto en campos string del JSON de delitos."""
    q = _norm(query or "")
    out: List[Dict[str, Any]] = []
    for idx, item in enumerate(CRIME_DATA):
        hay = False
        for v in item.values():
            if isinstance(v, str) and q in _norm(v):
                hay = True
                break
        if hay:
            out.append({
                "resource": f"crime://item/{idx}",
                "preview": {k: v for k, v in item.items() if isinstance(v, str)}
            })
            if len(out) >= top_k:
                break
    return out

@mcp.tool()
def crime_stats(
    query: str = "",
    estado: str = "",
    municipio: str = "",
    delito: str = "",
    year: int = 0,
    month: str = "",
    top_k: int = 10,
    min_count: float = 0.0,
) -> List[Dict[str, Any]]:
    """Filtra el JSON por estado/municipio/delito/año y devuelve conteos por mes o total.

    - Si 'month' está vacío, suma los 12 meses como 'total'.
    - Si 'month' viene, devuelve el conteo de ese mes en 'count'.
    - 'query' aplica sobre todos los campos de texto.
    """
    q = _norm(query)
    month_sel = month.strip()

    # Candidatos de nombres de campo (flexibles a acentos y mayúsculas)
    C_ESTADO = ["Entidad", "Estado"]
    C_MUNICIPIO = ["Municipio"]
    C_DELITO = ["Delito", "Tipo", "Categoria", "Clasificacion"]
    C_YEAR = ["Año", "Anio", "Year"]

    rows: List[Dict[str, Any]] = []
    for idx, item in enumerate(CRIME_DATA):
        # Filtro por query de texto
        if q:
            found = False
            for v in item.values():
                if isinstance(v, str) and q in _norm(v):
                    found = True
                    break
            if not found:
                continue

        v_estado = _get_first(item, C_ESTADO)
        v_muni = _get_first(item, C_MUNICIPIO)
        v_delito = _get_first(item, C_DELITO)
        v_year = _get_first(item, C_YEAR)

        if estado and (not v_estado or _norm(estado) not in _norm(str(v_estado))):
            continue
        if municipio and (not v_muni or _norm(municipio) not in _norm(str(v_muni))):
            continue
        if delito and (not v_delito or _norm(delito) not in _norm(str(v_delito))):
            continue
        if year and str(year) != str(v_year):
            continue

        if month_sel:
            count = _get_month_value(item, month_sel)
            if count < min_count:
                continue
            rows.append({
                "resource": f"crime://item/{idx}",
                "estado": v_estado,
                "municipio": v_muni,
                "delito": v_delito,
                "year": v_year,
                "month": month_sel,
                "count": count,
            })
        else:
            total = _sum_months(item)
            if total < min_count:
                continue
            rows.append({
                "resource": f"crime://item/{idx}",
                "estado": v_estado,
                "municipio": v_muni,
                "delito": v_delito,
                "year": v_year,
                "total": total,
            })

    # ordenar
    key = (lambda r: r.get("count", r.get("total", 0.0)))
    rows.sort(key=key, reverse=True)
    return rows[: max(1, top_k)]

@mcp.resource("crime://item/{index}")
def read_crime_item(index: str) -> Dict[str, Any]:
    try:
        i = int(index)
    except Exception:
        return {}
    if i < 0 or i >= len(CRIME_DATA):
        return {}
    return CRIME_DATA[i]

if __name__ == "__main__":
    mcp.run()
