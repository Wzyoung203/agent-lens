import pytest
from fastapi.testclient import TestClient

from agent_lens.api.app import create_app
from agent_lens.storage import SCHEMA_VERSION


@pytest.fixture
def client(seeded_db):
    app = create_app(seeded_db.execute("PRAGMA database_list").fetchone()[2])
    with TestClient(app) as test_client:
        yield test_client


def test_health_reports_schema_version(client):
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "schema_version": SCHEMA_VERSION}


def test_overview_endpoint_returns_cards_and_daily(client):
    response = client.get("/api/overview", params={"days": 30})

    assert response.status_code == 200
    body = response.json()
    assert body["cards"]["api_call_count"] == 3
    assert body["cards"]["total_tokens"] == 3850
    assert len(body["daily"]) == 3
    assert body["range"]["days"] == 30


def test_overview_rejects_out_of_range_days(client):
    assert client.get("/api/overview", params={"days": -1}).status_code == 422
    assert client.get("/api/overview", params={"days": 99999}).status_code == 422


def test_projects_endpoints(client):
    listing = client.get("/api/projects")
    assert listing.status_code == 200
    assert [item["project"] for item in listing.json()] == ["alpha", "beta"]

    detail = client.get("/api/projects/alpha")
    assert detail.status_code == 200
    body = detail.json()
    assert body["project"]["api_call_count"] == 2
    assert [item["session_id"] for item in body["sessions"]] == ["s1"]

    assert client.get("/api/projects/does-not-exist").status_code == 404


def test_sessions_endpoints(client):
    listing = client.get("/api/sessions", params={"limit": 10})
    assert listing.status_code == 200
    body = listing.json()
    assert body["total"] == 2
    assert body["limit"] == 10
    assert body["items"][0]["session_id"] == "s1"

    detail = client.get("/api/sessions/s1")
    assert detail.status_code == 200
    assert [turn["turn_id"] for turn in detail.json()["turns"]] == ["t1", "t2"]

    turn = client.get("/api/sessions/s1/turns/t1")
    assert turn.status_code == 200
    assert turn.json()["api_calls"][0]["priced"] is True

    assert client.get("/api/sessions/nope").status_code == 404
    assert client.get("/api/sessions/s1/turns/nope").status_code == 404


def test_tools_endpoints(client):
    response = client.get("/api/tools")

    assert response.status_code == 200
    body = response.json()
    stats = {item["name"]: item for item in body["stats"]}
    assert stats["exec_command"]["call_count"] == 2
    assert body["failure_cost"]["total"] == pytest.approx(0.0048)
    assert len(body["failures"]) == 1

    failures = client.get("/api/tools/failures", params={"limit": 1})
    assert failures.status_code == 200
    assert failures.json()["total"] == 1


def test_settings_pricing_round_trip(client):
    payload = {
        "provider": "deepseek",
        "model": "deepseek-flash",
        "effective_from": "2026-01-01T00:00:00+00:00",
        "time_window": "idle",
        "input_price_per_mtok": 0.15,
        "cached_input_price_per_mtok": 0.003,
        "output_price_per_mtok": 0.6,
    }

    created = client.put("/api/settings/pricing", json=payload)
    assert created.status_code == 200
    assert created.json()["model"] == "deepseek-flash"

    listed = client.get("/api/settings/pricing", params={"model": "deepseek-flash"})
    assert listed.status_code == 200
    assert [item["time_window"] for item in listed.json()] == ["idle"]

    deleted = client.delete(
        "/api/settings/pricing",
        params={
            "provider": "deepseek",
            "model": "deepseek-flash",
            "effective_from": "2026-01-01T00:00:00+00:00",
            "time_window": "idle",
        },
    )
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": True}
    assert client.get("/api/settings/pricing", params={"model": "deepseek-flash"}).json() == []


def test_settings_project_mapping_reassigns_sessions(client, tmp_path):
    created = client.put(
        "/api/settings/projects",
        json={"path_prefix": "/work/alpha", "project": "alpha-renamed"},
    )
    assert created.status_code == 200

    mappings = client.get("/api/settings/projects").json()
    renamed = next(item for item in mappings if item["project"] == "alpha-renamed")
    assert renamed["prefixes"] == ["/work/alpha"]
    assert renamed["session_count"] == 1

    deleted = client.delete(
        "/api/settings/projects", params={"path_prefix": "/work/alpha"}
    )
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    # 删掉映射后会话回到未归类
    assert any(
        item["project"] == "未归类" for item in client.get("/api/settings/projects").json()
    )


def test_settings_refresh_reports_reassignments(client):
    response = client.post("/api/settings/projects/refresh")

    assert response.status_code == 200
    assert response.json()["sessions_reassigned"] == 0


def test_settings_status_exposes_counts_and_queue(client):
    response = client.get("/api/settings/status")

    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == SCHEMA_VERSION
    assert body["counts"]["api_calls"] == 3
    assert body["report_queue"] == {"pending": 0, "sent": 0, "failed": 0}
    assert body["langfuse_enabled"] is False
