import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock
from dataclasses import replace

from commercial_agent import CommercialAgent, ProviderCommercialModel
from commercial_models import CommercialInterpretation, CommercialStatus
from diagnostics import PerformanceRecorder, COMMERCIAL_AGENT_PIPELINE_STAGES
from generation import AgentJob, GenerationStatus
from llm_client import LLMResponse
from rag_models import DocumentChunk, RetrievedChunk, RetrievalResult
from tool_registry import LocalToolRegistry
from rag_service import RAGService
from vector_store import LocalVectorStore
from streamlit.testing.v1 import AppTest


QUESTION = "El Hospital Costa Sur prevé consumir 4,8 GWh el próximo mes. Analiza las implicaciones comerciales según su contrato."
FIXTURE = Path(__file__).parent / "fixtures" / "hospital_costa_sur.json"


def contract_matches():
    return [RetrievedChunk(DocumentChunk(**row), .9)
            for row in json.loads(FIXTURE.read_text(encoding="utf-8"))]


class EvidenceModel:
    def __init__(self):
        self.calls = 0

    def interpret(self, question, evidence, calculations, timeout):
        self.calls += 1
        return CommercialInterpretation(findings=[
            {"source_number": e["source_number"], "quote": e["text"]}
            for e in evidence if any(term in e["text"].casefold()
                                     for term in ("disponibilidad", "spot +", "take-or-pay", "ttf"))
        ][:8])


class CommercialAgentTests(unittest.TestCase):
    def test_existing_rag_ingestion_and_retrieval_end_to_end(self):
        class Embeddings:
            model = "test-commercial"
            def embed_documents(self, texts):
                return [[1., 1.] for _ in texts]
            def embed_query(self, text):
                return [1., 1.]
        with TemporaryDirectory() as directory:
            service = RAGService(Embeddings(), LocalVectorStore(directory, Embeddings.model))
            text = "\n\n".join(m.chunk.text for m in contract_matches())
            service.ingest([("contrato_hospital_costa_sur.txt", text.encode("utf-8"))])
            result = CommercialAgent(EvidenceModel(), service).run(QUESTION, 30)
            self.assertEqual(result.calculations[0].result["contractual_excess_gwh"], .2)
            self.assertEqual(next(f.value for f in result.contract_facts
                                  if f.name == "excess_surcharge_eur_mwh"), 4)

    def run_agent(self, request=QUESTION, matches=None, model=None, registry=None):
        matches = contract_matches() if matches is None else matches
        self.rag = Mock()
        self.rag.retrieve.return_value = RetrievalResult(matches, "test")
        self.model = model or EvidenceModel()
        self.recorder = PerformanceRecorder("commercial-test", "test", "model", "commercial_agent")
        return CommercialAgent(self.model, self.rag, self.recorder, registry).run(request, 30)

    def test_reference_contract_facts_and_deterministic_excess(self):
        result = self.run_agent()
        self.rag.retrieve.assert_called_once()
        self.assertEqual(self.model.calls, 1)
        facts = {f.name: f for f in result.contract_facts}
        self.assertEqual(facts["reference_volume_gwh"].value, 4)
        self.assertEqual(facts["flexibility_percent"].value, 15)
        self.assertEqual(facts["excess_surcharge_eur_mwh"].value, 4)
        calc = result.calculations[0]
        self.assertEqual(calc.origin, "deterministic")
        self.assertEqual(calc.result["contractual_min_gwh"], 3.4)
        self.assertEqual(calc.result["contractual_max_gwh"], 4.6)
        self.assertEqual(calc.result["contractual_excess_gwh"], .2)
        self.assertEqual(calc.input_evidence["forecast_demand_gwh"], "Consulta del usuario")
        self.assertIn("Spot + 4", result.summary)
        self.assertNotIn("calculate_margin", result.tools_used)

    def test_contract_values_are_not_hard_coded(self):
        matches = [replace(m, chunk=replace(m.chunk, text=m.chunk.text.replace("4 GWh", "5 GWh")))
                   for m in contract_matches()]
        result = self.run_agent(matches=matches)
        self.assertEqual(result.calculations[0].result["contractual_max_gwh"], 5.75)
        self.assertEqual(result.calculations[0].result["contractual_excess_gwh"], 0)

    def test_friendly_sources_and_grounded_quotes(self):
        result = self.run_agent()
        for fact in result.contract_facts:
            self.assertTrue(any(fact.evidence in m.chunk.text for m in contract_matches()))
            self.assertEqual(fact.source.document_name, "contrato_hospital_costa_sur.txt")
            self.assertEqual(fact.source.page_start, 1)
        for match in contract_matches():
            self.assertNotIn(match.chunk.chunk_id, result.content)
            self.assertNotIn(match.chunk.chunk_id, result.model_dump_json())
        self.assertIn("disponibilidad", result.content)
        self.assertIn("trimestralmente", result.content)

    def test_invented_model_evidence_is_discarded(self):
        model = Mock()
        model.interpret.return_value = {"findings": [{"source_number": 1, "quote": "El exceso es gratuito"}]}
        result = self.run_agent(model=model)
        self.assertFalse(any("gratuito" in f.evidence for f in result.commercial_findings))
        self.assertNotIn("gratuito", result.content)
        self.assertEqual(result.status, CommercialStatus.PARTIAL)

    def test_no_contract_skips_model_and_requests_input(self):
        result = self.run_agent(matches=[])
        self.assertEqual(self.model.calls, 0)
        self.assertEqual(result.status, CommercialStatus.NEEDS_INPUT)
        self.assertFalse(result.contract_facts)
        self.assertFalse(result.calculations)

    def test_excess_evidence_is_retained_when_model_omits_it(self):
        model = Mock()
        model.interpret.return_value = {"findings": [{"source_number": 3, "quote": contract_matches()[2].chunk.text}]}
        result = self.run_agent(model=model)
        evidence = " ".join(f.evidence for f in result.commercial_findings)
        self.assertIn("disponibilidad", evidence)
        self.assertIn("Spot + 4", evidence)

    def test_wrong_customer_does_not_use_other_contract(self):
        result = self.run_agent(request=QUESTION.replace("Costa Sur", "Otro Centro"))
        self.assertEqual(result.status, CommercialStatus.NEEDS_INPUT)
        self.assertFalse(result.contract_facts)

    def test_annual_forecast_is_not_compared_to_monthly_flexibility(self):
        result = self.run_agent(request="El Hospital Costa Sur prevé consumir 48 GWh el próximo año. Analiza el contrato.")
        self.assertFalse(result.calculations)

    def test_retrieval_failure_is_structured(self):
        service = Mock()
        service.retrieve.side_effect = OSError("private endpoint")
        result = CommercialAgent(EvidenceModel(), service).run(QUESTION)
        self.assertEqual(result.status, CommercialStatus.FAILED)
        self.assertNotIn("private endpoint", result.content)

    def test_missing_terms_does_not_invent_excess(self):
        result = self.run_agent(matches=contract_matches()[:2])
        self.assertFalse(result.calculations)
        self.assertTrue(result.warnings)

    def test_margin_requires_complete_inputs(self):
        registry = Mock(wraps=LocalToolRegistry(("calculate_margin",)))
        result = self.run_agent(request=QUESTION + " Calcula el margen.", registry=registry)
        registry.execute.assert_not_called()
        self.assertTrue(any("coste de suministro" in w for w in result.warnings))
        self.assertTrue(result.calculations)

    def test_margin_executes_existing_registered_tool(self):
        registry = Mock(wraps=LocalToolRegistry(("calculate_margin",)))
        result = self.run_agent(request=QUESTION + " Calcula el margen con precio de venta de 50 EUR/MWh y coste de suministro de 30 EUR/MWh.", registry=registry)
        registry.execute.assert_called_once_with("calculate_margin", {
            "sales_price_eur_mwh": 50., "supply_cost_eur_mwh": 30., "volume_gwh": 4.8})
        margin = next(c for c in result.calculations if c.name == "calculate_margin")
        self.assertEqual(margin.result["total_margin_eur"], 96000)

    def test_base_contract_price_not_applied_to_excess_margin(self):
        registry = Mock(wraps=LocalToolRegistry(("calculate_margin",)))
        self.run_agent(request=QUESTION + " Calcula el margen con coste de suministro de 30 EUR/MWh.", registry=registry)
        registry.execute.assert_not_called()

    def test_model_failure_preserves_calculations(self):
        model = Mock()
        model.interpret.side_effect = ValueError("bad JSON")
        result = self.run_agent(model=model)
        self.assertEqual(result.calculations[0].result["contractual_excess_gwh"], .2)
        self.assertEqual(result.status, CommercialStatus.PARTIAL)

    def test_trace_exposes_actions_and_structured_result(self):
        self.run_agent()
        events = self.recorder.snapshot().events
        stages = {e.stage for e in events}
        self.assertTrue({"agent_start", "retrieved_context", "agent_decision", "tool_execution",
                         "llm_interpretation", "structured_result", "agent_final"} <= stages)
        self.assertIn("structured_result", COMMERCIAL_AGENT_PIPELINE_STAGES)
        self.assertIn("structured_result", next(e for e in events if e.stage == "structured_result").metadata)

    def test_agent_job_retains_structured_result(self):
        commercial = self.run_agent()
        job = AgentJob(SimpleNamespace(run=lambda *_: commercial), QUESTION, 30,
                       SimpleNamespace(provider_name="test", model="test", cancel=lambda: None))
        job._run()
        result = job.poll()
        self.assertEqual(result.status, GenerationStatus.COMPLETED)
        self.assertEqual(result.structured_result["customer"], "Hospital Costa Sur")

    def test_streamlit_result_and_pipeline_inspector(self):
        result = self.run_agent()
        self.recorder.finish(result.status.value)
        def page(data, snapshot):
            from commercial_models import CommercialAgentResult
            from commercial_ui import render_commercial_result
            from pipeline_inspector import render_pipeline_inspector
            render_commercial_result(CommercialAgentResult.model_validate(data))
            render_pipeline_inspector(snapshot)
        app = AppTest.from_function(page, args=(result.model_dump(mode="json"), self.recorder.snapshot()),
                                    default_timeout=15).run()
        self.assertFalse(app.exception)
        text = " ".join(item.value for item in app.markdown)
        self.assertIn("CommercialAgent", text)
        self.assertIn("Structured Commercial Result", text)
        self.assertTrue(app.dataframe)

    def test_provider_uses_one_call_without_tool_loop(self):
        provider = Mock()
        provider.generate_response.return_value = LLMResponse('{"source_numbers": []}')
        ProviderCommercialModel(provider).interpret(QUESTION, [], [], 30)
        provider.generate_response.assert_called_once()
        options = provider.generate_response.call_args.kwargs["options"]
        self.assertFalse(options.tool_calling_enabled)
        self.assertEqual(options.max_rounds, 1)

    def test_provider_resolves_selected_source_to_original_text(self):
        provider = Mock()
        provider.generate_response.return_value = LLMResponse('{"source_numbers": [1]}')
        result = ProviderCommercialModel(provider).interpret(QUESTION,
            [{"source_number": 1, "text": "Sujeto a disponibilidad."}], [], 30)
        self.assertEqual(result.findings[0].quote, "Sujeto a disponibilidad.")
        provider.generate_response.return_value = LLMResponse('{"source_numbers": [2]}')
        with self.assertRaises(ValueError):
            ProviderCommercialModel(provider).interpret(QUESTION,
                [{"source_number": 1, "text": "Sujeto a disponibilidad."}], [], 30)


if __name__ == "__main__":
    unittest.main()
