"""Search and discovery.

Three complementary modes over one catalog:

  * keyword  - FTS5 full-text across title, description, tags, metadata
  * filter   - faceted structured filters (media type, tag, status, dates)
  * semantic - embedding cosine ranking ("natural language" / more-like-this)

Facet counts cover all assets of the requested status, independently of the
current search conditions. The UI labels them accordingly.
"""
from __future__ import annotations

from . import ai
from .db import get_conn

SORTS = {
    "newest": "a.created_at DESC, a.id DESC",
    "oldest": "a.created_at ASC, a.id ASC",
    "name": "a.title ASC, a.id ASC",
    "largest": "a.size DESC, a.id DESC",
}


def _fts_query(q: str) -> str:
    """Turn user text into a safe FTS5 prefix query."""
    terms = [t for t in "".join(c if c.isalnum() or c.isspace() else " " for c in q).split() if t]
    return " ".join(f'"{t}"*' for t in terms)


def _query(*, q="", media_type=None, tag=None, collection_id=None,
           include_tag=None, exclude_tag=None, sort="newest", status="active"):
    """One predicate builder for counts, pages and exports; values stay bound."""
    if sort not in SORTS:
        raise ValueError("Unknown sort")
    where, params = ["a.status = ?"], [status]
    if q.strip():
        expression = _fts_query(q)
        if expression:
            where.append("a.id IN (SELECT rowid FROM asset_fts WHERE asset_fts MATCH ?)")
            params.append(expression)
        else:
            where.append("0")
    if media_type:
        where.append("a.media_type = ?")
        params.append(media_type)
    if collection_id is not None:
        where.append("EXISTS (SELECT 1 FROM collection_assets ca "
                     "WHERE ca.asset_id = a.id AND ca.collection_id = ?)")
        params.append(collection_id)
    included = {t.strip().lower() for t in [*(include_tag or []), *([tag] if tag else [])] if t.strip()}
    excluded = {t.strip().lower() for t in (exclude_tag or []) if t.strip()}
    if included & excluded:
        where.append("0")
    for names, negate in ((included, ""), (excluded, "NOT ")):
        for name in sorted(names):
            where.append(negate + "EXISTS (SELECT 1 FROM asset_tags at JOIN tags t ON t.id = at.tag_id "
                         "WHERE at.asset_id = a.id AND t.name = ?)")
            params.append(name)
    return " AND ".join(where), params, SORTS[sort]


def search(*, q="", media_type=None, tag=None, collection_id=None,
           include_tag=None, exclude_tag=None, status="active", sort="newest",
           mode="keyword", limit=60, offset=0) -> dict:
    if not 1 <= limit <= 200 or offset < 0:
        raise ValueError("Invalid pagination")
    # Retained for existing internal callers; the quiz UI/API use keyword mode.
    if mode == "semantic" and q.strip():
        return _semantic_search(q, media_type, status, limit, offset)
    where, params, order = _query(q=q, media_type=media_type, tag=tag,
        collection_id=collection_id, include_tag=include_tag, exclude_tag=exclude_tag,
        status=status, sort=sort)
    conn = get_conn()
    total = conn.execute(f"SELECT COUNT(*) c FROM assets a WHERE {where}", params).fetchone()["c"]
    rows = conn.execute(f"SELECT a.* FROM assets a WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?",
                        [*params, limit, offset]).fetchall()
    return {"total": total, "items": [_card(dict(r)) for r in rows],
            "facets": _facets(status), "mode": mode}


def export_rows(**filters) -> list[dict]:
    """All matches in list order, with sorted tag arrays and no pagination."""
    where, params, order = _query(**filters)
    conn = get_conn()
    rows = [dict(r) for r in conn.execute(
        f"SELECT a.* FROM assets a WHERE {where} ORDER BY {order}", params)]
    tags = {}
    for row in conn.execute(
        f"SELECT at.asset_id, t.name FROM asset_tags at JOIN tags t ON t.id = at.tag_id "
        f"JOIN assets a ON a.id = at.asset_id WHERE {where} ORDER BY t.name", params):
        tags.setdefault(row["asset_id"], []).append(row["name"])
    for row in rows:
        row["tags"] = tags.get(row["id"], [])
    return rows


def _semantic_search(q, media_type, status, limit, offset) -> dict:
    conn = get_conn()
    qvec = ai.embed(q)
    where = ["status = ?", "embedding IS NOT NULL"]
    params: list = [status]
    if media_type:
        where.append("media_type = ?")
        params.append(media_type)
    rows = conn.execute(
        f"SELECT * FROM assets WHERE {' AND '.join(where)}", params
    ).fetchall()
    scored = []
    for r in rows:
        score = ai.cosine(qvec, ai.unpack_embedding(r["embedding"]))
        scored.append((score, r))
    scored.sort(key=lambda x: x[0], reverse=True)
    page = scored[offset:offset + limit]
    items = []
    for score, r in page:
        card = _card(dict(r))
        card["score"] = round(score, 4)
        items.append(card)
    return {"total": len(scored), "items": items, "facets": _facets(status), "mode": "semantic"}


def more_like(asset_id: int, limit: int = 12) -> list[dict]:
    conn = get_conn()
    base = conn.execute("SELECT embedding FROM assets WHERE id = ?", (asset_id,)).fetchone()
    if not base or not base["embedding"]:
        return []
    bvec = ai.unpack_embedding(base["embedding"])
    rows = conn.execute(
        "SELECT * FROM assets WHERE id != ? AND status = 'active' AND embedding IS NOT NULL",
        (asset_id,),
    ).fetchall()
    scored = [(ai.cosine(bvec, ai.unpack_embedding(r["embedding"])), r) for r in rows]
    scored.sort(key=lambda x: x[0], reverse=True)
    out = []
    for score, r in scored[:limit]:
        card = _card(dict(r))
        card["score"] = round(score, 4)
        out.append(card)
    return out


def _facets(status: str) -> dict:
    conn = get_conn()
    media = conn.execute(
        "SELECT media_type, COUNT(*) c FROM assets WHERE status = ? GROUP BY media_type ORDER BY c DESC",
        (status,),
    ).fetchall()
    tags = conn.execute(
        """SELECT t.name, COUNT(*) c FROM tags t
           JOIN asset_tags at ON at.tag_id = t.id
           JOIN assets a ON a.id = at.asset_id AND a.status = ?
           GROUP BY t.name ORDER BY c DESC LIMIT 25""",
        (status,),
    ).fetchall()
    return {
        "media_types": [{"value": r["media_type"], "count": r["c"]} for r in media],
        "tags": [{"value": r["name"], "count": r["c"]} for r in tags],
    }


def _card(a: dict) -> dict:
    """Compact asset representation for grid/list views."""
    return {
        "id": a["id"],
        "uuid": a["uuid"],
        "title": a["title"],
        "filename": a["filename"],
        "media_type": a["media_type"],
        "mime_type": a["mime_type"],
        "ext": a["ext"],
        "size": a["size"],
        "width": a["width"],
        "height": a["height"],
        "alt_text": a["alt_text"],
        "status": a["status"],
        "created_at": a["created_at"],
    }
