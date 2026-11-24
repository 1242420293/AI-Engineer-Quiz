"""Reproducible quiz catalog: isolated in-memory DB, no uploaded files needed."""
import csv
import io
import json
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from keepstack import db, ingest
from keepstack.app import app
from keepstack.auth import hash_password
from keepstack.config import config


@pytest.fixture
def catalog(monkeypatch, tmp_path):
    # Sequential requests share one isolated connection, including API worker threads.
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    monkeypatch.setattr(db, "_local", SimpleNamespace(conn=conn))
    for key, value in {"data_dir": tmp_path, "db_path": tmp_path / "test.db",
                       "blob_dir": tmp_path / "blobs", "thumb_dir": tmp_path / "thumbs",
                       "cache_dir": tmp_path / "cache", "ai_enabled": False,
                       "ocr_enabled": False}.items():
        monkeypatch.setattr(config, key, value)
    db.init_db()
    conn.execute("INSERT INTO users (id,username,password_hash,role,created_at) VALUES (1,'admin',?,'admin','2026')",
                 (hash_password("admin"),))
    conn.execute("INSERT INTO collections (id,uuid,name,created_at) VALUES (1,'c1','Campaign','2026')")
    for tid, name in enumerate(["approved", "campaign", "internal", "approved-extra", '标,签 "A"'], 1):
        conn.execute("INSERT INTO tags (id,name) VALUES (?,?)", (tid, name.lower()))
    for aid in range(1, 48):
        conn.execute("""INSERT INTO assets
            (id,uuid,sha256,filename,title,description,media_type,status,storage_key,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (aid, f"asset-{aid}", str(aid), f"素材,{aid}.txt", "Harbor 港口", '中文, "引号"\n第二行',
             "image" if aid % 2 else "document", "deleted" if aid == 47 else "active",
             str(aid), f"2026-01-{1 + aid // 20:02d}T00:00:00Z", "2026"))
        tids = [4] if aid == 46 else [1, 2, 5]
        if aid % 7 == 0:
            tids.append(3)
        conn.executemany("INSERT INTO asset_tags (asset_id,tag_id) VALUES (?,?)", [(aid, t) for t in tids])
        if aid <= 40:
            conn.execute("INSERT INTO collection_assets (collection_id,asset_id) VALUES (1,?)", (aid,))
    conn.commit()
    for aid in range(1, 48):
        ingest.reindex_fts(aid)
    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        client.headers["Authorization"] = "Bearer " + login.json()["token"]
        yield client
    conn.close()


def read_csv(response):
    assert response.status_code == 200, response.text
    assert response.content.startswith(b"\xef\xbb\xbf")
    assert "attachment" in response.headers["content-disposition"]
    return list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"), newline="")))


def test_combined_filters_and_exact_tags(catalog):
    params = [("q", "harbor"), ("collection_id", "1"), ("media_type", "image"),
              ("include_tag", " APPROVED "), ("include_tag", "campaign"),
              ("include_tag", "approved"), ("exclude_tag", "internal")]
    result = catalog.get("/api/assets", params=params).json()
    expected = [f"asset-{i}" for i in range(39, 0, -2) if i % 7]
    assert [r["uuid"] for r in result["items"]] == expected
    assert result["total"] == len(expected)
    exported = read_csv(catalog.get("/api/assets/export.csv", params=params))
    assert [r["uuid"] for r in exported] == expected
    assert catalog.get("/api/assets", params={"tag": "approved"}).json()["total"] == 45
    assert catalog.get("/api/assets", params={"collection_id": 999}).json()["total"] == 0


@pytest.mark.parametrize("sort,reverse", [("newest", True), ("oldest", False)])
def test_pages_ties_and_export_agree(catalog, sort, reverse):
    params = [("q", "harbor"), ("include_tag", "approved"), ("exclude_tag", "internal"), ("sort", sort)]
    uuids = []
    expected = [f"asset-{i}" for i in sorted(range(1, 46), reverse=reverse) if i % 7]
    for offset in range(0, len(expected), 9):
        result = catalog.get("/api/assets", params=params + [("limit", "9"), ("offset", str(offset))]).json()
        assert result["total"] == len(expected)
        uuids.extend(r["uuid"] for r in result["items"])
    assert uuids == expected
    rows = read_csv(catalog.get("/api/assets/export.csv", params=params + [("limit", "1"), ("offset", "20")]))
    assert [r["uuid"] for r in rows] == uuids
    assert rows[0]["description"] == '中文, "引号"\n第二行'
    assert json.loads(rows[0]["tags"]) == ["approved", "campaign", '标,签 "a"']
    assert "asset-47" not in uuids


@pytest.mark.parametrize("params", [
    {"include_tag": "approved", "exclude_tag": "APPROVED"}, {"q": "!!!"},
    {"q": "' OR 1=1 --"}, {"include_tag": "does-not-exist"},
])
def test_empty_results_keep_csv_header(catalog, params):
    assert catalog.get("/api/assets", params=params).json()["total"] == 0
    response = catalog.get("/api/assets/export.csv", params=params)
    assert read_csv(response) == []
    assert response.content.decode("utf-8-sig").splitlines() == [
        "uuid,title,description,filename,media_type,tags,created_at"]


def test_blank_unicode_and_multiple_exclusions(catalog):
    assert catalog.get("/api/assets", params={"q": " \t "}).json()["total"] == 46
    assert catalog.get("/api/assets", params={"q": "港口"}).json()["total"] == 46
    result = catalog.get("/api/assets", params=[("exclude_tag", "internal"), ("exclude_tag", "approved-extra")]).json()
    assert result["total"] == 39
    result = catalog.get("/api/assets", params={"include_tag": '标,签 "A"'}).json()
    assert result["total"] == 45


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": -1}, {"limit": 201},
    {"offset": -1}, {"offset": "bad"}, {"collection_id": 0}, {"sort": "bad"}, {"status": "deleted"}])
def test_invalid_parameters(catalog, params):
    assert catalog.get("/api/assets", params=params).status_code == 422


def test_authentication_and_page_beyond_end(catalog):
    assert catalog.get("/api/assets", params={"offset": 999}).json()["items"] == []
    catalog.headers.clear()
    assert catalog.get("/api/assets").status_code == 401
    assert catalog.get("/api/assets/export.csv").status_code == 401


def test_edits_update_search_filters_and_export(catalog):
    response = catalog.patch("/api/assets/asset-1", json={"title": "Moonneedle", "description": "Newneedle"})
    assert response.status_code == 200
    assert all(r["uuid"] != "asset-1" for r in catalog.get("/api/assets", params={"q": "harbor"}).json()["items"])
    for keyword in ("Moonneedle", "Newneedle"):
        assert catalog.get("/api/assets", params={"q": keyword}).json()["total"] == 1
    assert catalog.post("/api/assets/asset-1/tags", json={"name": "Freshneedle"}).status_code == 200
    rows = read_csv(catalog.get("/api/assets/export.csv", params={"q": "Freshneedle", "include_tag": "freshneedle"}))
    assert len(rows) == 1 and rows[0]["title"] == "Moonneedle"
    assert rows[0]["description"] == "Newneedle"
    assert catalog.delete("/api/assets/asset-1/tags/approved").status_code == 200
    assert catalog.get("/api/assets", params={"q": "Moonneedle", "include_tag": "approved"}).json()["total"] == 0
    assert read_csv(catalog.get("/api/assets/export.csv", params={"q": "Moonneedle", "include_tag": "approved"})) == []
    assert catalog.delete("/api/assets/asset-1/tags/freshneedle").status_code == 200
    assert catalog.get("/api/assets", params={"q": "Freshneedle"}).json()["total"] == 0
