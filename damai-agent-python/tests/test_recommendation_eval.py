from __future__ import annotations

import unittest
from decimal import Decimal
from pathlib import Path

from damai_agent.recommendation_eval import (
    RecommendationEvalCase,
    evaluate_recommendations,
    load_recommendation_eval_cases,
)


class RecommendationOfflineEvalTest(unittest.TestCase):
    def test_fixture_passes_all_release_red_lines(self) -> None:
        fixture = Path(__file__).parent / "fixtures" / "recommendation_eval.json"
        report = evaluate_recommendations(load_recommendation_eval_cases(fixture))
        self.assertTrue(report.passed_red_lines)
        self.assertEqual(report.routing_accuracy, 1.0)
        self.assertEqual(report.budget_enforcement_rate, 1.0)
        self.assertEqual(report.preference_accuracy, 1.0)
        self.assertEqual(report.live_verification_rate, 1.0)

    def test_budget_relaxation_and_wrong_route_fail_the_gate(self) -> None:
        report = evaluate_recommendations(
            (
                RecommendationEvalCase(
                    case_id="missing-live-tool",
                    user_text="推荐 500 元以内的音乐剧",
                    model_arguments={"maxPrice": 800},
                    allowed_tool_names=("search_programs",),
                    expected_tool_name="search_programs",
                    expected_max_price=Decimal("500"),
                ),
            )
        )
        self.assertFalse(report.passed_red_lines)
        self.assertEqual(report.budget_enforcement_rate, 1.0)
        self.assertEqual(report.live_verification_rate, 0.0)
