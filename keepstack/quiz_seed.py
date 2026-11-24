"""Optional reproducible quiz demo: python -m keepstack.quiz_seed.

Use a fresh KEEPSTACK_DATA_DIR to keep this dataset apart from personal uploads.
Running again reuses existing content and the named collection.
"""
import io
import uuid

from PIL import Image

from . import ingest
from .audit import now_iso
from .db import get_conn, init_db


def main():
    init_db()
    conn = get_conn()
    name = "Quiz Harbor Campaign"
    row = conn.execute("SELECT id FROM collections WHERE name = ?", (name,)).fetchone()
    if row:
        cid = row["id"]
    else:
        cid = conn.execute("INSERT INTO collections (uuid,name,created_at) VALUES (?,?,?)",
                           (str(uuid.uuid4()), name, now_iso())).lastrowid
        conn.commit()
    for index in range(45):
        if index % 5 == 0:
            data = f"Quiz demo document {index}: harbor 港口".encode("utf-8")
            filename = f"quiz_harbor_{index:02d}.txt"
        else:
            buf = io.BytesIO()
            Image.new("RGB", (120, 80), (index * 5, 80, 180)).save(buf, format="PNG")
            data, filename = buf.getvalue(), f"quiz_harbor_{index:02d}.png"
        asset = ingest.ingest_stream(io.BytesIO(data), filename, title=f"Harbor 港口 {index:02d}", run_ai=False)
        aid = asset["id"]
        tags = ["approved", "campaign"] + (["internal"] if index % 7 == 0 else [])
        ingest._set_tags(conn, aid, [(t, 1.0) for t in tags], "manual")
        conn.execute("UPDATE assets SET description=?, created_at=? WHERE id=?",
                     ('中文, "quoted"\n第二行', f"2026-01-{1 + index // 15:02d}T00:00:00Z", aid))
        conn.execute("INSERT OR IGNORE INTO collection_assets (collection_id,asset_id,added_at) VALUES (?,?,?)",
                     (cid, aid, now_iso()))
        conn.commit()
        ingest.reindex_fts(aid)
    print(f"45 quiz assets ready in collection '{name}' (id={cid}).")
    print("harbor + approved + campaign - internal: 38 matches, including 31 images.")
    print("Start the app to bootstrap admin/admin when using an empty data directory.")


if __name__ == "__main__":
    main()
