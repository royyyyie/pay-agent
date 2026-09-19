from __future__ import annotations

import argparse
import json

from damai_agent.recommendation_eval import (
    evaluate_recommendations,
    load_recommendation_eval_cases,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate recommendation safety red lines")
    parser.add_argument("--eval-set", required=True)
    return parser.parse_args()


def main() -> int:
    report = evaluate_recommendations(load_recommendation_eval_cases(parse_args().eval_set))
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    return 0 if report.passed_red_lines else 1


if __name__ == "__main__":
    raise SystemExit(main())
