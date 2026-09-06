"""Central deterministic Owner-Gate policy.

Structured ActionIntent metadata is authoritative. Descriptions are retained
for audit/debugging only and never downgrade a material action. This module is
the single policy implementation used by the local API and CLI; cloud
components must call the same endpoint or import this classifier rather than
define a second policy.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, StrictBool

POLICY_VERSION = "DOCADOX_REPORTER_OWNER_GATE_POLICY_V1"


class Decision(str, Enum):
    ROUTINE = "ROUTINE"
    OWNER_GATE = "OWNER_GATE"


class ActionType(str, Enum):
    READ = "READ"
    SEARCH = "SEARCH"
    INSPECT = "INSPECT"
    EDIT_IN_SCOPE = "EDIT_IN_SCOPE"
    BUG_FIX = "BUG_FIX"
    REFACTOR = "REFACTOR"
    TEST = "TEST"
    LINT = "LINT"
    TYPECHECK = "TYPECHECK"
    BUILD_LOCAL = "BUILD_LOCAL"
    LOCAL_SERVICE = "LOCAL_SERVICE"
    BROWSER_VERIFY = "BROWSER_VERIFY"
    MOCK_API = "MOCK_API"
    DISPOSABLE_DATABASE = "DISPOSABLE_DATABASE"
    LOCAL_MIGRATION_TEST = "LOCAL_MIGRATION_TEST"
    INSTALL_DEV_DEPENDENCY = "INSTALL_DEV_DEPENDENCY"
    DOC_UPDATE = "DOC_UPDATE"
    REPO_HYGIENE = "REPO_HYGIENE"
    GIT_INSPECT = "GIT_INSPECT"
    PREPARE_GIT_ACTION = "PREPARE_GIT_ACTION"
    GIT_STAGE = "GIT_STAGE"
    GIT_COMMIT = "GIT_COMMIT"
    GIT_PUSH = "GIT_PUSH"
    GIT_MERGE = "GIT_MERGE"
    DEPLOY = "DEPLOY"
    PRODUCTION_ACTION = "PRODUCTION_ACTION"
    DESTRUCTIVE_ACTION = "DESTRUCTIVE_ACTION"
    FROZEN_GOVERNANCE_CHANGE = "FROZEN_GOVERNANCE_CHANGE"
    AUTHORITY_CHANGE = "AUTHORITY_CHANGE"
    LIFECYCLE_SEMANTICS_CHANGE = "LIFECYCLE_SEMANTICS_CHANGE"
    SCIENTIFIC_SEMANTICS_CHANGE = "SCIENTIFIC_SEMANTICS_CHANGE"
    SECURITY_BOUNDARY_CHANGE = "SECURITY_BOUNDARY_CHANGE"
    MATERIAL_PRODUCT_DECISION = "MATERIAL_PRODUCT_DECISION"
    SECRET_CREDENTIAL_ACTION = "SECRET_CREDENTIAL_ACTION"
    OTHER = "OTHER"


class TargetEnvironment(str, Enum):
    LOCAL = "LOCAL"
    DISPOSABLE = "DISPOSABLE"
    NON_PRODUCTION = "NON_PRODUCTION"
    PRODUCTION = "PRODUCTION"


class ActionIntent(BaseModel):
    """Strict caller-supplied intent; booleans can only escalate a decision."""

    model_config = ConfigDict(extra="forbid")

    action_type: ActionType
    target_environment: TargetEnvironment = TargetEnvironment.LOCAL
    authorized_scope: StrictBool = True
    destructive: StrictBool = False
    irreversible: StrictBool = False
    force_push_or_history_rewrite: StrictBool = False
    changes_frozen_governance: StrictBool = False
    changes_authority: StrictBool = False
    changes_lifecycle_semantics: StrictBool = False
    changes_scientific_semantics: StrictBool = False
    material_security_boundary_change: StrictBool = False
    unresolved_material_product_choice: StrictBool = False
    requires_owner_secret_action: StrictBool = False
    material_risk_unknown: StrictBool = False
    description: Optional[str] = Field(default=None, max_length=1000)


class PolicyResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Decision
    reason_code: str
    reason: str
    policy_version: str = POLICY_VERSION
    action_type: ActionType
    requires_explicit_owner_approval: bool


_GOVERNANCE_GATES = {
    ActionType.FROZEN_GOVERNANCE_CHANGE: ("OWNER_GATE_FROZEN_GOVERNANCE", "changes Frozen governance authority"),
    ActionType.AUTHORITY_CHANGE: ("OWNER_GATE_AUTHORITY_CHANGE", "changes authority, roles, capabilities, or grant/revoke semantics"),
    ActionType.LIFECYCLE_SEMANTICS_CHANGE: ("OWNER_GATE_LIFECYCLE_SEMANTICS", "changes canonical lifecycle semantics"),
    ActionType.SCIENTIFIC_SEMANTICS_CHANGE: ("OWNER_GATE_SCIENTIFIC_SEMANTICS", "changes scientific-decision meaning or authority"),
    ActionType.SECURITY_BOUNDARY_CHANGE: ("OWNER_GATE_SECURITY_BOUNDARY", "changes a material authentication, authorization, or trust boundary"),
    ActionType.MATERIAL_PRODUCT_DECISION: ("OWNER_GATE_MATERIAL_PRODUCT_DECISION", "requires an unresolved material product decision"),
    ActionType.SECRET_CREDENTIAL_ACTION: ("OWNER_GATE_SECRET_ACTION", "requires Owner provision, disclosure, rotation, or authorization of a real secret"),
}


def _gate(intent: ActionIntent, code: str, reason: str) -> PolicyResult:
    return PolicyResult(
        decision=Decision.OWNER_GATE, reason_code=code, reason=reason,
        action_type=intent.action_type, requires_explicit_owner_approval=True,
    )


def _routine(intent: ActionIntent, code: str, reason: str) -> PolicyResult:
    return PolicyResult(
        decision=Decision.ROUTINE, reason_code=code, reason=reason,
        action_type=intent.action_type, requires_explicit_owner_approval=False,
    )


def classify_action(intent: ActionIntent) -> PolicyResult:
    """Classify one intent with material-risk precedence."""
    if not intent.authorized_scope:
        return _gate(intent, "OWNER_GATE_OUT_OF_SCOPE", "action exceeds the authorized task, repository, or environment scope")

    if intent.force_push_or_history_rewrite:
        return _gate(intent, "OWNER_GATE_FORCE_PUSH", "force push or destructive history rewrite requires explicit approval")

    if intent.changes_frozen_governance:
        return _gate(intent, "OWNER_GATE_FROZEN_GOVERNANCE", "structured intent marks a Frozen governance change")
    if intent.changes_authority:
        return _gate(intent, "OWNER_GATE_AUTHORITY_CHANGE", "structured intent marks an authority change")
    if intent.changes_lifecycle_semantics:
        return _gate(intent, "OWNER_GATE_LIFECYCLE_SEMANTICS", "structured intent marks a lifecycle semantic change")
    if intent.changes_scientific_semantics:
        return _gate(intent, "OWNER_GATE_SCIENTIFIC_SEMANTICS", "structured intent marks a scientific semantic change")
    if intent.material_security_boundary_change:
        return _gate(intent, "OWNER_GATE_SECURITY_BOUNDARY", "structured intent marks a material security-boundary change")
    if intent.unresolved_material_product_choice:
        return _gate(intent, "OWNER_GATE_MATERIAL_PRODUCT_DECISION", "structured intent marks an unresolved material product choice")
    if intent.requires_owner_secret_action:
        return _gate(intent, "OWNER_GATE_SECRET_ACTION", "structured intent requires a real Owner secret or credential action")
    if intent.material_risk_unknown:
        return _gate(intent, "OWNER_GATE_AMBIGUOUS_MATERIAL_RISK", "material risk is explicitly unresolved")

    if intent.action_type in _GOVERNANCE_GATES:
        code, reason = _GOVERNANCE_GATES[intent.action_type]
        return _gate(intent, code, reason)
    if intent.action_type is ActionType.DESTRUCTIVE_ACTION or intent.destructive or intent.irreversible:
        return _gate(intent, "OWNER_GATE_DESTRUCTIVE_ACTION", "destructive or irreversible action requires explicit approval")
    if intent.action_type is ActionType.OTHER:
        return _gate(intent, "OWNER_GATE_AMBIGUOUS_MATERIAL_RISK", "unclassified action cannot be assumed routine")

    if intent.target_environment is TargetEnvironment.PRODUCTION:
        return _routine(intent, "ROUTINE_AUTHORIZED_PRODUCTION_DEPLOY", "authorized production action has no unresolved material-risk flags")
    if intent.action_type in {ActionType.GIT_COMMIT}:
        return _routine(intent, "ROUTINE_GIT_COMMIT", "ordinary authorized source commit")
    if intent.action_type is ActionType.GIT_PUSH:
        return _routine(intent, "ROUTINE_NORMAL_PUSH", "ordinary authorized non-force push")
    if intent.action_type is ActionType.DEPLOY:
        return _routine(intent, "ROUTINE_STAGING_DEPLOY", "authorized non-production deployment")
    if intent.action_type in {ActionType.TEST, ActionType.LINT, ActionType.TYPECHECK, ActionType.BUILD_LOCAL,
                              ActionType.DISPOSABLE_DATABASE, ActionType.LOCAL_MIGRATION_TEST}:
        return _routine(intent, "ROUTINE_LOCAL_TEST", "local or disposable verification activity")
    if intent.action_type in {ActionType.EDIT_IN_SCOPE, ActionType.BUG_FIX, ActionType.REFACTOR}:
        return _routine(intent, "ROUTINE_IN_SCOPE_EDIT", "authorized in-scope technical change")
    if intent.action_type in {ActionType.GIT_STAGE, ActionType.GIT_MERGE, ActionType.PREPARE_GIT_ACTION}:
        return _routine(intent, "ROUTINE_GIT_ACTION", "ordinary authorized Git operation")
    return _routine(intent, f"ROUTINE_{intent.action_type.value}", "authorized routine technical action")
