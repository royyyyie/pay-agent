from __future__ import annotations

import copy

from scripts.check_contract_compatibility import check_compatibility, load_document
from scripts.generate_tool_schemas import CONTRACT_PATH


def test_current_contract_is_compatible_with_frozen_v1_baseline() -> None:
    baseline = load_document(CONTRACT_PATH.parent / "baselines/agent-tools-v1.0.0.openapi.yaml")
    candidate = load_document(CONTRACT_PATH)

    assert check_compatibility(baseline, candidate) == []


def test_removed_operation_is_reported_as_breaking() -> None:
    baseline = load_document(CONTRACT_PATH)
    candidate = copy.deepcopy(baseline)
    candidate["paths"].pop("/internal/agent/v1/tools/programs/detail")

    errors = check_compatibility(baseline, candidate)

    assert any("operation was removed" in error for error in errors)


def test_new_required_request_property_is_reported_as_breaking() -> None:
    baseline = load_document(CONTRACT_PATH)
    candidate = copy.deepcopy(baseline)
    candidate["components"]["schemas"]["ProgramSearchRequest"]["required"] = ["keyword"]

    errors = check_compatibility(baseline, candidate)

    assert any("new required input property" in error for error in errors)
