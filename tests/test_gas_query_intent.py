import unittest

from diagnostics import PerformanceRecorder
from gas_query_intent import classify_gas_query
from gas_query_orchestration import prepare_gas_query
from gas_response_handling import GasResponseContract
from gas_type_resolution import resolve_gas_type
from rag_models import DocumentChunk, RetrievalResult, RetrievedChunk


class StubRAGService:
    def __init__(self, retrieval: RetrievalResult) -> None:
        self.retrieval = retrieval

    def retrieve(self, question: str) -> RetrievalResult:
        return self.retrieval


class GasQueryIntentTests(unittest.TestCase):
    def test_contractual_questions_are_documentary(self) -> None:
        questions = (
            "¿Cuál es la flexibilidad del Hospital Costa Sur?",
            "¿Cuál es el take-or-pay?",
            "¿Cómo se calcula el precio del exceso?",
            "¿Cuándo puede revisarse el precio contractual?",
            "¿Cuál es el rango mensual permitido sin penalización?",
        )
        for question in questions:
            with self.subTest(question=question):
                self.assertEqual(
                    classify_gas_query(question).intent, "documentary"
                )

    def test_two_gwh_values_and_position_are_quantitative(self) -> None:
        result = classify_gas_query(
            "Tenemos 4,3 GWh aprovisionados y esperamos consumir 4,8 GWh. "
            "Analiza nuestra posición."
        )
        self.assertEqual(result.intent, "quantitative")

    def test_percent_and_spot_price_with_scenario_are_quantitative(self) -> None:
        result = classify_gas_query(
            "¿Qué ocurre en un escenario de demanda +10% si el spot está a "
            "42 €/MWh?"
        )
        self.assertEqual(result.intent, "quantitative")

    def test_ambiguous_query_without_numbers_defaults_to_documentary(self) -> None:
        result = classify_gas_query(
            "Según el contrato, explica la demanda y el suministro."
        )
        self.assertEqual(result.intent, "documentary")


class GasQueryOrchestrationTests(unittest.TestCase):
    def test_documentary_flow_never_runs_input_parser_or_tools(self) -> None:
        question = (
            "¿Cuál es la flexibilidad de consumo del Hospital Costa Sur y "
            "cuál es el rango mensual permitido sin penalización?"
        )
        retrieval = _retrieval(question)
        recorder = _recorder("documentary")
        prepared = prepare_gas_query(
            question, recorder, StubRAGService(retrieval)
        )
        self.assertEqual(prepared.intent.intent, "documentary")
        self.assertIsNone(prepared.quantitative_analysis)
        self.assertEqual(
            prepared.response_contract, GasResponseContract.DOCUMENT_TEXT
        )
        stages = {event.stage for event in recorder.snapshot().events}
        self.assertNotIn("input_parsing", stages)
        self.assertNotIn("scenario_generation", stages)
        self.assertNotIn("tool_execution", stages)
        self.assertIn("query_classification", stages)
        self.assertIn(retrieval.matches[0].chunk.chunk_id, prepared.messages[0]["content"])

    def test_quantitative_flow_reuses_existing_deterministic_analysis(self) -> None:
        question = (
            "Gas natural con demanda prevista de 120 GWh y 95 GWh de "
            "suministro contratado. Analiza la posición con coste de "
            "suministro 32 EUR/MWh, precio de venta 45 EUR/MWh y spot "
            "42 EUR/MWh."
        )
        recorder = _recorder("quantitative")
        prepared = prepare_gas_query(
            question, recorder, StubRAGService(_retrieval(question))
        )
        self.assertEqual(prepared.intent.intent, "quantitative")
        self.assertIsNotNone(prepared.quantitative_analysis)
        self.assertEqual(
            prepared.response_contract, GasResponseContract.SCENARIO_JSON
        )
        stages = {event.stage for event in recorder.snapshot().events}
        self.assertIn("input_parsing", stages)
        self.assertIn("scenario_generation", stages)
        self.assertIn("tool_execution", stages)

    def test_position_and_spot_continue_without_margin_inputs(self) -> None:
        question = (
            "El Hospital Costa Sur prevé consumir 4,8 GWh el próximo mes. "
            "Tenemos 4,3 GWh de gas ya aprovisionado para este cliente y el "
            "precio actual del mercado spot es de 42 €/MWh. Utilizando las "
            "condiciones de su contrato, analiza nuestra posición de "
            "aprovisionamiento, la flexibilidad contractual, el volumen que "
            "tendremos que cubrir y el impacto económico."
        )
        recorder = _recorder("partial-position")
        prepared = prepare_gas_query(
            question, recorder, StubRAGService(_retrieval(question))
        )

        self.assertEqual(
            prepared.response_contract,
            GasResponseContract.QUANTITATIVE_TEXT,
        )
        executions = prepared.quantitative_analysis.tool_executions
        self.assertEqual(
            [item["name"] for item in executions],
            [
                "calculate_supply_position",
                "calculate_spot_exposure",
                "calculate_margin",
            ],
        )
        self.assertEqual(executions[0]["result"]["position_gwh"], -0.5)
        self.assertEqual(executions[1]["result"]["exposure_eur"], 21_000)
        self.assertEqual(executions[2]["status"], "skipped")
        self.assertEqual(
            executions[2]["missing_inputs"],
            ["supply_cost_eur_mwh", "sales_price_eur_mwh"],
        )
        events = recorder.snapshot().events
        self.assertFalse(
            [event for event in events if event.stage == "scenario_generation"]
        )
        margin_event = next(
            event
            for event in events
            if event.metadata.get("tool_name") == "calculate_margin"
        )
        self.assertEqual(margin_event.status.value, "skipped")
        self.assertIn("21000", prepared.messages[0]["content"])
        self.assertIn("500 MWh", prepared.messages[0]["content"])
        self.assertIn("500 MWh * 42 €/MWh = 21000 €", prepared.messages[0]["content"])
        self.assertIn('"contractual_max_gwh": 4.6', prepared.messages[0]["content"])
        self.assertIn('"forecast_demand_gwh": 4.8', prepared.messages[0]["content"])
        self.assertIn('"contractual_excess_gwh": 0.2', prepared.messages[0]["content"])
        self.assertIn('"short_position_gwh": 0.5', prepared.messages[0]["content"])


def _retrieval(question: str) -> RetrievalResult:
    chunk = DocumentChunk(
        chunk_id="0123456789abcdef:0123456789ab",
        document_id="0123456789abcdef",
        document_name="contrato_hospital_costa_sur.txt",
        section="Flexibilidad",
        page_start=1,
        page_end=1,
        ordinal=0,
        text=(
            "Contrato de gas natural. La flexibilidad mensual permitida sin "
            "penalización es del 15 %."
        ),
    )
    reference = DocumentChunk(
        chunk_id="0123456789abcdef:reference",
        document_id="0123456789abcdef",
        document_name="contrato_hospital_costa_sur.txt",
        section="Volumen contratado",
        page_start=1,
        page_end=1,
        ordinal=1,
        text="El consumo mensual de referencia será de 4 GWh.",
    )
    surcharge = DocumentChunk(
        chunk_id="0123456789abcdef:surcharge",
        document_id="0123456789abcdef",
        document_name="contrato_hospital_costa_sur.txt",
        section="Excesos",
        page_start=1,
        page_end=1,
        ordinal=2,
        text="Precio Spot + 4 €/MWh.",
    )
    resolution = resolve_gas_type(
        question, [chunk.text, reference.text, surcharge.text]
    )
    return RetrievalResult(
        [
            RetrievedChunk(chunk, 0.9),
            RetrievedChunk(reference, 0.8),
            RetrievedChunk(surcharge, 0.7),
        ],
        "fake-embedding",
        resolution,
    )


def _recorder(operation_id: str) -> PerformanceRecorder:
    return PerformanceRecorder(
        operation_id, "lmstudio", "test", "gas_analysis"
    )


if __name__ == "__main__":
    unittest.main()
