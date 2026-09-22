from datetime import timedelta
import json
from pathlib import Path
import tempfile
import unittest

from industrial_gases import (
    ExtractedSupplyFacts,
    Quantity,
    ResolvedSupplyIdentity,
)
from scripts.evaluate_industrial_gases_llm import (
    CASES,
    ExpectedFacts,
    ExpectedQuantity,
    ExpectedRate,
    REFERENCE_TIME,
    compare_facts,
    comparison_counts,
    evaluate_cases,
    filter_cases,
    summarize,
    write_json_report,
)


class IndustrialGasesEvaluationHarnessTests(unittest.TestCase):
    def test_comparison_reports_match_missing_unexpected_and_mismatch(self):
        expected = ExpectedFacts(
            current_inventory=ExpectedQuantity(3200, "kg"),
            consumption_rate=ExpectedRate(700, "kg"),
            planned_delivery_in_days=4,
            safety_stock=ExpectedQuantity(1500, "kg"),
        )
        facts = ExtractedSupplyFacts(
            current_inventory=Quantity(3200, "kg"),
            planned_delivery_quantity=Quantity(4000, "kg"),
            planned_delivery_at=REFERENCE_TIME + timedelta(days=5),
            safety_stock=Quantity(1500, "kg"),
        )

        comparison = compare_facts(expected, facts, ResolvedSupplyIdentity(), REFERENCE_TIME)

        self.assertEqual(comparison["current_inventory"], "MATCH")
        self.assertEqual(comparison["consumption_rate"], "MISSING")
        self.assertEqual(comparison["planned_delivery_quantity"], "UNEXPECTED")
        self.assertEqual(comparison["planned_delivery_in_days"], "MISMATCH")
        counts = comparison_counts(comparison)
        self.assertEqual(counts, {
            "matched": 2, "missing": 1, "unexpected": 1, "mismatched": 1,
        })
        self.assertEqual(summarize([{"deterministic": {
            "comparison_counts": counts,
            "grounding_issues": [], "operational_error": None,
            "latency_ms": 1.0,
        }}], ("deterministic",))["deterministic"]["mismatched"], 1)

    def test_corpus_has_unique_ids_fixed_timezone_and_all_categories(self):
        self.assertEqual(len(CASES), 15)
        self.assertEqual(len({case.id for case in CASES}), len(CASES))
        self.assertTrue({case.category for case in CASES}.issubset({
            "structured_easy", "natural_phrasing", "missing_facts",
            "unsupported_ambiguous", "typographic_units", "identity",
        }))
        self.assertIsNotNone(REFERENCE_TIME.utcoffset())
        self.assertTrue(all(case.reference_time == REFERENCE_TIME for case in CASES))

    def test_case_and_category_filters(self):
        self.assertEqual([case.id for case in filter_cases(CASES, case_id="IG-03")], ["IG-03"])
        natural = filter_cases(CASES, category="natural_phrasing")
        self.assertEqual({case.id for case in natural}, {"IG-03", "IG-04", "IG-05"})
        with self.assertRaises(ValueError):
            filter_cases(CASES, case_id="IG-99")

    def test_deterministic_only_needs_no_provider_and_emits_no_llm_result(self):
        record = evaluate_cases((CASES[0],), deterministic_only=True)[0]
        self.assertIn("deterministic", record)
        self.assertNotIn("llm", record)
        self.assertEqual(record["deterministic"]["service"]["state"], "COMPLETED")

    def test_json_report_is_machine_readable_and_keeps_run_metadata(self):
        records = [{"id": "IG-01", "expected": {"inventory": "3200 kg"}}]
        summary = {"deterministic": {"cases": 1, "matched": 1}}
        provider = {"name": "lmstudio", "model": "local-model", "timeout_seconds": 30}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "result.json"
            write_json_report(path, records, summary, provider, "deterministic_only")
            decoded = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(decoded["run_metadata"]["provider"], provider)
        self.assertEqual(decoded["run_metadata"]["mode"], "deterministic_only")
        self.assertEqual(decoded["cases"], records)
        self.assertEqual(decoded["summary"], summary)


if __name__ == "__main__":
    unittest.main()
