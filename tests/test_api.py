def test_unknown_agent_id_is_rejected(client):
    r = client.post("/v1/events", json={
        "agent_id": "not-a-configured-agent",
        "task_id": "task-x", "status": "PASS", "summary": "hi",
    })
    assert r.status_code == 403
    assert r.json()["detail"] == "unknown_agent_id"


def test_known_agent_id_is_accepted(client, telegram):
    r = client.post("/v1/events", json={
        "agent_id": "claude-1", "task_id": "task-x", "status": "PASS", "summary": "hi",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["notified"] is True
    assert len(telegram.sent) == 1


def test_invalid_status_value_is_rejected(client):
    r = client.post("/v1/events", json={
        "agent_id": "claude-1", "task_id": "task-x", "status": "NOT_A_REAL_STATUS", "summary": "hi",
    })
    assert r.status_code == 422


def test_missing_required_field_is_rejected(client):
    r = client.post("/v1/events", json={"agent_id": "claude-1", "status": "PASS"})
    assert r.status_code == 422


def test_extra_unexpected_field_is_rejected_strict_schema(client):
    r = client.post("/v1/events", json={
        "agent_id": "claude-1", "task_id": "task-x", "status": "PASS", "summary": "hi",
        "unexpected_field": "should not be accepted",
    })
    assert r.status_code == 422


def test_status_endpoint_reports_no_secret_values(client, settings):
    client.post("/v1/events", json={
        "agent_id": "claude-1", "task_id": "task-x", "status": "PASS", "summary": "hi",
    })
    r = client.get("/v1/status")
    assert r.status_code == 200
    body_text = r.text
    assert settings.bot_token not in body_text
    assert settings.chat_id not in body_text


def test_health_endpoint_never_reveals_configuration_values(client):
    r = client.get("/v1/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "telegram_configured": True}
