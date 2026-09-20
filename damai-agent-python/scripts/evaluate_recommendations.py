from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from damai_agent.evidence import write_report
from damai_agent.recommendation_eval import (
    evaluate_recommendations,
    load_recommendation_eval_asset,
)

DEFAULT_CONTRACT = Path(__file__).resolve().parents[2] / "contracts" / "agent-tools-v1.openapi.yaml"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate recommendation safety red lines")
    parser.add_argument("--eval-set", required=True)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--min-eval-cases", type=int, default=100)
    parser.add_argument("--require-approved-eval", action="store_true")
    parser.add_argument("--report-out", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    contract = args.contract.resolve()
    if not contract.is_file() or contract.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("recommendation contract must be a bounded file")
    contract_sha256 = hashlib.sha256(contract.read_bytes()).hexdigest()
    asset = load_recommendation_eval_asset(args.eval_set)
    if args.require_approved_eval:
        asset.require_release_eligible(
            minimum_case_count=args.min_eval_cases,
            contract_sha256=contract_sha256,
        )
    governance = asset.governance_payload(
        required=args.require_approved_eval,
        minimum_case_count=args.min_eval_cases,
        contract_sha256=contract_sha256,
    )
    evaluation = evaluate_recommendations(asset.cases)
    passed = evaluation.passed_red_lines and (
        not args.require_approved_eval or governance["releaseEligible"] is True
    )
    payload: dict[str, object] = {
        "schemaVersion": "damai.recommendation.acceptance/v1",
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "evalSetSha256": hashlib.sha256(Path(args.eval_set).resolve().read_bytes()).hexdigest(),
        "contractSha256": contract_sha256,
        "evalGovernance": governance,
        "quality": evaluation.to_dict(),
        "passed": passed,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if args.report_out is not None:
        write_report(args.report_out, payload)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
