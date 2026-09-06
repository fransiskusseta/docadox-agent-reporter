from __future__ import annotations

import json

import pytest

import reporter_cli
from reporter.owner_gate_policy import ActionIntent, ActionType, Decision, TargetEnvironment, classify_action


@pytest.mark.parametrize("action_type", [
    ActionType.READ, ActionType.SEARCH, ActionType.INSPECT, ActionType.EDIT_IN_SCOPE,
    ActionType.BUG_FIX, ActionType.REFACTOR, ActionType.TEST, ActionType.LINT,
    ActionType.TYPECHECK, ActionType.BUILD_LOCAL, ActionType.LOCAL_SERVICE,
    ActionType.BROWSER_VERIFY, ActionType.MOCK_API, ActionType.DISPOSABLE_DATABASE,
    ActionType.LOCAL_MIGRATION_TEST, ActionType.INSTALL_DEV_DEPENDENCY,
    ActionType.DOC_UPDATE, ActionType.REPO_HYGIENE, ActionType.GIT_INSPECT,
    ActionType.PREPARE_GIT_ACTION, ActionType.GIT_STAGE, ActionType.GIT_COMMIT,
    ActionType.GIT_PUSH, ActionType.GIT_MERGE,
])
def test_authorized_ordinary_actions_are_routine(action_type):
    result = classify_action(ActionIntent(action_type=action_type))
    assert result.decision is Decision.ROUTINE
    assert result.requires_explicit_owner_approval is False


@pytest.mark.parametrize("action_type,reason_code", [
    (ActionType.FROZEN_GOVERNANCE_CHANGE, "OWNER_GATE_FROZEN_GOVERNANCE"),
    (ActionType.AUTHORITY_CHANGE, "OWNER_GATE_AUTHORITY_CHANGE"),
    (ActionType.LIFECYCLE_SEMANTICS_CHANGE, "OWNER_GATE_LIFECYCLE_SEMANTICS"),
    (ActionType.SCIENTIFIC_SEMANTICS_CHANGE, "OWNER_GATE_SCIENTIFIC_SEMANTICS"),
    (ActionType.SECURITY_BOUNDARY_CHANGE, "OWNER_GATE_SECURITY_BOUNDARY"),
    (ActionType.MATERIAL_PRODUCT_DECISION, "OWNER_GATE_MATERIAL_PRODUCT_DECISION"),
    (ActionType.SECRET_CREDENTIAL_ACTION, "OWNER_GATE_SECRET_ACTION"),
    (ActionType.DESTRUCTIVE_ACTION, "OWNER_GATE_DESTRUCTIVE_ACTION"),
    (ActionType.OTHER, "OWNER_GATE_AMBIGUOUS_MATERIAL_RISK"),
])
def test_material_action_types_are_owner_gates(action_type, reason_code):
    result = classify_action(ActionIntent(action_type=action_type))
    assert result.decision is Decision.OWNER_GATE
    assert result.reason_code == reason_code
    assert result.requires_explicit_owner_approval is True


def test_authorized_reversible_production_deploy_is_routine():
    result = classify_action(ActionIntent(
        action_type=ActionType.DEPLOY, target_environment=TargetEnvironment.PRODUCTION,
    ))
    assert result.decision is Decision.ROUTINE
    assert result.reason_code == "ROUTINE_AUTHORIZED_PRODUCTION_DEPLOY"


@pytest.mark.parametrize("intent,reason_code", [
    (ActionIntent(action_type=ActionType.EDIT_IN_SCOPE, changes_frozen_governance=True), "OWNER_GATE_FROZEN_GOVERNANCE"),
    (ActionIntent(action_type=ActionType.BUG_FIX, material_security_boundary_change=True), "OWNER_GATE_SECURITY_BOUNDARY"),
    (ActionIntent(action_type=ActionType.GIT_COMMIT, changes_authority=True), "OWNER_GATE_AUTHORITY_CHANGE"),
    (ActionIntent(action_type=ActionType.GIT_PUSH, force_push_or_history_rewrite=True), "OWNER_GATE_FORCE_PUSH"),
    (ActionIntent(action_type=ActionType.DEPLOY, target_environment=TargetEnvironment.PRODUCTION, destructive=True), "OWNER_GATE_DESTRUCTIVE_ACTION"),
    (ActionIntent(action_type=ActionType.PRODUCTION_ACTION, material_risk_unknown=True), "OWNER_GATE_AMBIGUOUS_MATERIAL_RISK"),
    (ActionIntent(action_type=ActionType.TEST, authorized_scope=False), "OWNER_GATE_OUT_OF_SCOPE"),
])
def test_material_flags_override_routine_action_type(intent, reason_code):
    result = classify_action(intent)
    assert result.decision is Decision.OWNER_GATE
    assert result.reason_code == reason_code


def test_policy_version_and_result_shape():
    result = classify_action(ActionIntent(action_type=ActionType.TEST))
    assert result.policy_version == "DOCADOX_REPORTER_OWNER_GATE_POLICY_V1"
    assert set(result.model_dump()) == {
        "decision", "reason_code", "reason", "policy_version", "action_type",
        "requires_explicit_owner_approval",
    }


def test_loopback_api_classifies_and_rejects_malformed(client):
    routine = client.post("/v1/owner-gate/classify", json={
        "action_type": "GIT_COMMIT", "target_environment": "LOCAL",
    })
    assert routine.status_code == 200
    assert routine.json()["decision"] == "ROUTINE"
    assert routine.json()["reason_code"] == "ROUTINE_GIT_COMMIT"

    gate = client.post("/v1/owner-gate/classify", json={
        "action_type": "GIT_PUSH", "target_environment": "LOCAL",
        "force_push_or_history_rewrite": True,
    })
    assert gate.status_code == 200
    assert gate.json()["decision"] == "OWNER_GATE"
    assert gate.json()["reason_code"] == "OWNER_GATE_FORCE_PUSH"

    missing = client.post("/v1/owner-gate/classify", json={"target_environment": "LOCAL"})
    unsupported = client.post("/v1/owner-gate/classify", json={"action_type": "NOT_REAL"})
    extra = client.post("/v1/owner-gate/classify", json={"action_type": "TEST", "unexpected": True})
    assert missing.status_code == unsupported.status_code == extra.status_code == 422


def test_cli_classify_action_does_not_require_telegram(monkeypatch, capsys):
    captured = {}

    def fake_request(method, path, base_url, body=None):
        captured.update(method=method, path=path, body=body)
        return {"decision": "ROUTINE", "reason_code": "ROUTINE_LOCAL_TEST"}

    monkeypatch.setattr(reporter_cli, "_request", fake_request)
    reporter_cli.main(["classify-action", "--action-type", "TEST", "--environment", "LOCAL"])
    assert captured == {
        "method": "POST", "path": "/v1/owner-gate/classify",
        "body": {"action_type": "TEST", "target_environment": "LOCAL"},
    }
    assert json.loads(capsys.readouterr().out)["decision"] == "ROUTINE"


def test_cli_force_push_classification_is_owner_gate(monkeypatch, capsys):
    def fake_request(method, path, base_url, body=None):
        assert body["force_push_or_history_rewrite"] is True
        return {"decision": "OWNER_GATE", "reason_code": "OWNER_GATE_FORCE_PUSH"}

    monkeypatch.setattr(reporter_cli, "_request", fake_request)
    reporter_cli.main(["classify-action", "--action-type", "GIT_PUSH", "--force-push-or-history-rewrite"])
    assert json.loads(capsys.readouterr().out)["decision"] == "OWNER_GATE"
