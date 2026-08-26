import json
import unittest

from diagnostics import PerformanceRecorder, PerformanceStatus
from gas_analysis import GasAnalysisError, ScenarioResult
from gas_response_handling import GasResponseContract, handle_gas_response


class GasResponseHandlingTests(unittest.TestCase):
    def test_documentary_plain_text_never_runs_json_validation(self) -> None:
        recorder = _recorder("document-text")
        response = handle_gas_response(
            GasResponseContract.DOCUMENT_TEXT,
            "La flexibilidad contractual es del 15 %.",
            recorder,
            [],
            [],
        )
        self.assertEqual(
            response.documentary_content,
            "La flexibilidad contractual es del 15 %.",
        )
        stages = [event.stage for event in recorder.snapshot().events]
        self.assertNotIn("parse_validation", stages)
        self.assertIn("final_response", stages)

    def test_documentary_markdown_is_accepted_as_text(self) -> None:
        recorder = _recorder("document-markdown")
        response = handle_gas_response(
            GasResponseContract.DOCUMENT_TEXT,
            "**Flexibilidad:** 15 %\n\n- Mínimo: 3,4 GWh",
            recorder,
            [],
            [],
        )
        self.assertIn("**Flexibilidad:**", response.documentary_content)
        self.assertFalse(
            [
                event
                for event in recorder.snapshot().events
                if event.stage == "parse_validation"
            ]
        )

    def test_empty_documentary_response_has_controlled_error(self) -> None:
        recorder = _recorder("document-empty")
        with self.assertRaisesRegex(GasAnalysisError, "no devolvió contenido"):
            handle_gas_response(
                GasResponseContract.DOCUMENT_TEXT,
                "  ",
                recorder,
                [],
                [],
            )
        final_event = next(
            event
            for event in recorder.snapshot().events
            if event.stage == "final_response"
        )
        self.assertEqual(final_event.status, PerformanceStatus.FAILED)

    def test_quantitative_contract_keeps_json_and_pydantic_validation(self) -> None:
        recorder = _recorder("scenario-json")
        content = json.dumps(
            {
                "summary": "Resumen",
                "key_risks": ["Riesgo"],
                "recommendation": "Recomendación",
            }
        )
        response = handle_gas_response(
            GasResponseContract.SCENARIO_JSON,
            content,
            recorder,
            [_scenario()],
            [],
        )
        self.assertEqual(response.scenario_analysis.summary, "Resumen")
        stages = [event.stage for event in recorder.snapshot().events]
        self.assertIn("parse_validation", stages)
        self.assertIn("final_response", stages)


def _scenario() -> ScenarioResult:
    return ScenarioResult(
        name="Base",
        demand_variation_percent=0,
        demand_gwh=4,
        supply_position_gwh=0,
        short_position_gwh=0,
        spot_price_eur_mwh=42,
        spot_exposure_eur=0,
        estimated_margin_eur=100,
    )


def _recorder(operation_id: str) -> PerformanceRecorder:
    return PerformanceRecorder(
        operation_id, "lmstudio", "test", "gas_analysis"
    )


if __name__ == "__main__":
    unittest.main()
