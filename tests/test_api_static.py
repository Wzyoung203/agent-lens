from fastapi.testclient import TestClient

from agent_lens.api.app import create_app


def _web_dist(tmp_path):
    web = tmp_path / "dist"
    (web / "assets").mkdir(parents=True)
    (web / "index.html").write_text("<!doctype html><div id=app></div>", encoding="utf-8")
    (web / "assets" / "app.js").write_text("console.log('ok')", encoding="utf-8")
    return web


def test_static_dir_serves_index_and_assets(tmp_path, seeded_db):
    app = create_app(seeded_db.execute("PRAGMA database_list").fetchone()[2],
                     static_dir=_web_dist(tmp_path))

    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert "id=app" in client.get("/").text
        asset = client.get("/assets/app.js")
        assert asset.status_code == 200
        assert "console.log" in asset.text


def test_spa_fallback_returns_index_for_client_routes(tmp_path, seeded_db):
    app = create_app(seeded_db.execute("PRAGMA database_list").fetchone()[2],
                     static_dir=_web_dist(tmp_path))

    with TestClient(app) as client:
        # Vue Router 的 history 模式：直接刷新深层路由要拿到 index.html
        assert client.get("/sessions/s1").status_code == 200
        assert "id=app" in client.get("/sessions/s1").text
        assert client.get("/unknown/deep/path").status_code == 200


def test_unknown_api_path_is_404_not_index(tmp_path, seeded_db):
    app = create_app(seeded_db.execute("PRAGMA database_list").fetchone()[2],
                     static_dir=_web_dist(tmp_path))

    with TestClient(app) as client:
        response = client.get("/api/does-not-exist")

    assert response.status_code == 404
    assert "id=app" not in response.text


def test_no_static_dir_means_api_only(tmp_path, seeded_db):
    app = create_app(seeded_db.execute("PRAGMA database_list").fetchone()[2])

    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/").status_code == 404
