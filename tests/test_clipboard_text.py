import unittest
from dataclasses import dataclass
from types import SimpleNamespace

from clipboard_text import (
    build_all_clipboard_text,
    build_diagnostics_clipboard_text,
    build_response_clipboard_text,
    build_tool_executions_clipboard_text,
    build_business_copy_payload,
    format_alternative_evaluation,
)
from api_client_models import ApiAnalysisResult
from diagnostics import (
    PerformanceEvent,
    PerformanceSnapshot,
    PerformanceStatus,
)


def event(stage, metadata=None, duration=0.01, status=PerformanceStatus.COMPLETED):
    return PerformanceEvent(
        event_id=stage,
        operation_id="op-123",
        stage=stage,
        status=status,
        duration_seconds=duration,
        metadata=metadata or {},
    )


def snapshot(events=(), mode="gas_analysis"):
    return PerformanceSnapshot(
        operation_id="op-123",
        provider="lmstudio",
        model="qwen/qwen3-8b",
        mode=mode,
        status="completed",
        started_at=0,
        elapsed_seconds=8.47,
        events=tuple(events),
    )


@dataclass
class Scenario:
    name: str = "Base"
    demand_gwh: float = 4.8
    supply_position_gwh: float = -0.5
    short_position_gwh: float = 0.5
    spot_price_eur_mwh: float = 42
    spot_exposure_eur: float = 21_000
    estimated_margin_eur: float = 10_000


@dataclass
class Analysis:
    scenarios: tuple = (Scenario(),)
    summary: str = "Cobertura insuficiente."
    key_risks: tuple = ("Exposición spot",)
    recommendation: str = "Comprar cobertura."


class ClipboardResponseTests(unittest.TestCase):
    def test_business_copy_includes_structured_decision_plan_without_duplicates(self):
        result = ApiAnalysisResult.model_validate({
            "operation_id": "op-plan", "status": "completed", "summary": "Resumen",
            "routing": {"selected_agents": ["ProcurementAgent"], "skipped_agents": [],
                        "method": "deterministic", "reasons": []},
            "diagnostics": {"llm_calls": 0, "rag_calls": 0, "tool_calls": 2},
            "recommendation": {
                "action": "Cubrir SHORT.", "is_complete": True,
                "actions": [{"id": "operational-short", "category": "operational", "action": "Cubrir SHORT.",
                              "rationale": "Rationale visible una vez.", "supporting_metrics": [["SHORT", "0.5 GWh"]]}],
                "decision_plan": {"steps": [{"id": "operational-short", "category": "operational",
                    "action": "Cubrir SHORT.", "rationale": "No repetir", "supporting_metrics": [["SHORT", "0.5 GWh"]],
                    "horizon": "current_period", "decision_state": "review_required", "depends_on": [],
                    "missing_information": [], "source_action_id": "operational-short", "source_agent": "ProcurementAgent",
                    "alternatives": [{"id": "operational-short-full", "label": "Evaluar cobertura completa",
                                      "description": "Evaluar la cobertura completa del SHORT operativo.",
                                      "source_step_id": "operational-short"},
                                     {"id": "operational-short-partial", "label": "Evaluar cobertura parcial",
                                      "description": "Evaluar una cobertura parcial del SHORT operativo.",
                                      "source_step_id": "operational-short"}]}],
                    "is_complete": True, "warnings": []},
            },
        })
        text = build_business_copy_payload(result)
        self.assertIn("PLAN DE DECISIÓN", text)
        self.assertIn("[PERIODO ACTUAL · OPERATIVA]", text)
        self.assertLess(text.index("PLAN DE DECISIÓN"), text.index("ACCIONES RECOMENDADAS"))
        self.assertEqual(text.count("Rationale visible una vez."), 1)
        self.assertIn("Opciones a considerar:", text)
        self.assertIn("Evaluar cobertura completa", text)
        self.assertIn("Evaluar la cobertura completa del SHORT operativo.", text)
        self.assertNotIn("operational-short-full", text)
        self.assertNotIn("source_step_id", text)

    def test_decision_dependency_mapping_preserves_unknown_ids(self):
        from clipboard_text import (decision_dependency_label, decision_missing_information_label,
                                     decision_readiness_label)
        self.assertEqual(decision_dependency_label("operational-short"), "Cobertura del SHORT operativo")
        self.assertEqual(decision_dependency_label("synthetic-source"), "synthetic-source")
        self.assertEqual(decision_readiness_label("READY"), "Completa")
        self.assertEqual(decision_readiness_label("PARTIALLY_READY"), "Parcial")
        self.assertEqual(decision_readiness_label("BLOCKED"), "Insuficiente")
        self.assertEqual(decision_missing_information_label("spot_price_eur_mwh"), "Precio spot")
        self.assertEqual(decision_missing_information_label("synthetic-input"), "synthetic-input")

    def test_business_copy_renders_alternative_evaluation_without_internal_keys(self):
        evaluation = {"alternative_id": "operational-short-partial", "status": "EVALUATED",
                      "inputs": {"coverage_volume_gwh": 0.3, "coverage_price_eur_mwh": 40},
                      "outcomes": [{"metric": "covered_volume_gwh", "value": 0.3, "unit": "GWh", "origin": "scenario_input"},
                                   {"metric": "coverage_cost_eur", "value": 12000, "unit": "EUR", "origin": "derived"}],
                      "missing_inputs": []}
        alternative = {"id": "operational-short-partial", "label": "Evaluar cobertura parcial",
                       "description": "Evaluar una cobertura parcial.", "source_step_id": "operational-short",
                       "evaluation": evaluation}
        payload = {"operation_id": "op-eval", "status": "completed", "summary": "Resumen",
                   "routing": {"selected_agents": ["ProcurementAgent"], "skipped_agents": [], "method": "deterministic", "reasons": []},
                   "diagnostics": {"llm_calls": 0, "rag_calls": 0, "tool_calls": 2},
                   "recommendation": {"action": "Revisar", "is_complete": True,
                                      "decision_plan": {"is_complete": True, "steps": [{
                                          "id": "operational-short", "category": "operational", "action": "Cubrir",
                                          "alternatives": [alternative]}]}}}
        result = ApiAnalysisResult.model_validate(payload)
        text = build_business_copy_payload(result)
        self.assertIn("Evaluación: Calculada", text)
        self.assertIn("Volumen de cobertura: 0.3 GWh", text)
        self.assertIn("Precio de cobertura: 40 €/MWh", text)
        self.assertIn("Coste de cobertura: 12,000 €", text)
        self.assertNotIn("coverage_volume_gwh", text)

    def test_alternative_evaluation_missing_inputs_use_business_labels(self):
        evaluation = SimpleNamespace(
            status="NOT_EVALUATED",
            inputs=None,
            missing_inputs=("coverage_volume_gwh", "coverage_price_eur_mwh"),
            outcomes=(),
        )
        lines = format_alternative_evaluation(evaluation)
        text = "\n".join(lines)
        self.assertIn("Volumen de cobertura", text)
        self.assertIn("Precio de cobertura", text)
        self.assertNotIn("coverage_volume_gwh", text)
        self.assertNotIn("coverage_price_eur_mwh", text)
        self.assertNotIn("operational-short-partial", text)
        self.assertNotIn("scenario_input", text)
    def test_plain_response_preserves_markdown_and_line_breaks(self):
        content = "## Resultado\n\nLínea uno\nLínea dos"
        self.assertEqual(build_response_clipboard_text(content), content)

    def test_structured_response_matches_visible_human_sections(self):
        text = build_response_clipboard_text(Analysis())
        self.assertIn("Base", text)
        self.assertIn("Resumen\n-------\nCobertura insuficiente.", text)
        self.assertIn("- Exposición spot", text)
        self.assertIn("Recomendación", text)
        self.assertNotIn("{'scenarios':", text)

    def test_copy_all_contains_response_tools_and_diagnostics(self):
        text = build_all_clipboard_text(
            "Posición SHORT de 25 GWh.",
            [{
                "name": "calculate_supply_position",
                "arguments": {"expected_demand_gwh": 120},
                "result": {"interpretation": "SHORT", "position_gwh": -25},
                "elapsed_seconds": 0.00001,
            }],
            snapshot(mode="procurement_planner"),
        )
        self.assertIn("Response\n--------\nPosición SHORT de 25 GWh.", text)
        self.assertIn("Tools used\n----------", text)
        self.assertIn("calculate_supply_position", text)
        self.assertIn('"position_gwh": -25', text)
        self.assertIn("Diagnostics\n-----------", text)
        self.assertIn("ID: op-123", text)

    def test_tool_copy_is_safe_with_missing_fields_and_secrets(self):
        text = build_tool_executions_clipboard_text([
            {"name": "tool", "arguments": {"api_key": "sk-supersecret123"}},
            None,
        ])
        self.assertIn("[REDACTED]", text)
        self.assertNotIn("supersecret", text)


class ClipboardDiagnosticsTests(unittest.TestCase):
    def test_provider_model_configuration_and_timings(self):
        text = build_diagnostics_clipboard_text(
            snapshot(
                [
                    event("provider_start", {"max_tokens": 384, "max_output_tokens": 256, "timeout_seconds": 60, "thinking_enabled": False}),
                    event("llm_call", {"call_number": 1, "purpose": "generation", "request_setup_seconds": 0.2, "response_stream_seconds": 8.1, "server_inference_seconds": None}, 8.3),
                ]
            )
        )
        self.assertIn("Provider: lmstudio", text)
        self.assertIn("Model: qwen/qwen3-8b", text)
        self.assertIn("Max tokens: 384", text)
        self.assertIn("Thinking: disabled", text)
        self.assertIn("LLM calls: 1", text)
        self.assertIn("Request wall time: 8.30 s", text)
        self.assertIn("Server inference time: unavailable", text)

    def test_tool_execution_and_skipped_tool(self):
        text = build_diagnostics_clipboard_text(
            snapshot(
                [
                    event("tool_execution", {"tool_name": "calculate_supply_position", "tool_arguments": {"expected_demand_gwh": 4.8}, "tool_result": {"interpretation": "SHORT"}, "tool_seconds": 0.0002}, 0.0002),
                    event("tool_execution", {"tool_name": "calculate_spot_exposure", "skip_reason": "missing_inputs", "missing_inputs": ["spot_price_eur_mwh"]}, 0, PerformanceStatus.SKIPPED),
                ]
            )
        )
        self.assertIn("calculate_supply_position", text)
        self.assertIn('"interpretation": "SHORT"', text)
        self.assertIn("status: skipped", text)
        self.assertIn("reason: missing_inputs", text)

    def test_agent_skipped_tool_and_reason_are_in_diagnostics(self):
        text = build_diagnostics_clipboard_text(
            snapshot(
                [
                    event(
                        "agent_observation",
                        {
                            "observation_type": "tool_skipped",
                            "tool_name": "calculate_spot_exposure",
                            "message": "Spot exposure is unnecessary for a non-SHORT position.",
                        },
                        duration=0,
                    )
                ],
                mode="procurement_planner",
            )
        )
        self.assertIn("calculate_spot_exposure", text)
        self.assertIn("status: skipped", text)
        self.assertIn("unnecessary for a non-SHORT position", text)

    def test_tool_diagnostics_use_core_execution_time_not_wrapper_time(self):
        text = build_diagnostics_clipboard_text(
            snapshot([
                event(
                    "tool_execution",
                    {
                        "tool_name": "calculate_supply_position",
                        "tool_call_count": 1,
                        "tool_seconds": 0.000011,
                    },
                    duration=0.0818,
                )
            ])
        )
        self.assertIn("duration: 0.011 ms", text)
        self.assertIn(
            "calculate_supply_position ........ 0.011 ms", text
        )
        self.assertNotIn("81.8 ms", text)

    def test_rag_uses_friendly_sources_and_scores_without_chunk_ids(self):
        text = build_diagnostics_clipboard_text(
            snapshot(
                [
                    event("query_embedding", duration=0.01),
                    event("vector_search", {"top_k": 4}, 0.04),
                    event("retrieved_context", {"persisted_document_count": 1, "persisted_chunk_count": 14, "sources": [{"document_name": "Contrato Hospital Costa Sur", "section": "Cláusula 4 Flexibilidad", "chunk_id": "deadbeef:abc123", "score": 0.82}]}),
                ]
            )
        )
        self.assertIn("Documents indexed: 1", text)
        self.assertIn("Chunks: 14", text)
        self.assertIn("Top-K: 4", text)
        self.assertIn("Contrato Hospital Costa Sur · Cláusula 4 Flexibilidad", text)
        self.assertIn("Score: 0.82", text)
        self.assertNotIn("deadbeef:abc123", text)

    def test_missing_optional_fields_do_not_fail(self):
        text = build_diagnostics_clipboard_text(snapshot())
        self.assertIn("Operation", text)
        self.assertIn("LLM calls: 0", text)

    def test_sensitive_values_are_redacted(self):
        text = build_diagnostics_clipboard_text(
            snapshot([event("tool_execution", {"tool_name": "bad", "tool_arguments": {"api_key": "sk-supersecret123", "authorization": "Bearer tokenvalue"}})])
        )
        self.assertNotIn("supersecret", text)
        self.assertNotIn("tokenvalue", text)
        self.assertIn("[REDACTED]", text)

    def test_formatting_has_no_execution_side_effects(self):
        events = [event("tool_execution", {"tool_name": "calculate_supply_position"})]
        before = list(events)
        build_diagnostics_clipboard_text(snapshot(events))
        self.assertEqual(events, before)

    def test_agent_summary_contains_observable_actions_not_chain_of_thought(self):
        agent_snapshot = snapshot(
            [
                event("llm_call", {"call_number": 1, "purpose": "agent_decision"}, 1),
                event("agent_decision", {"action": "call_tool", "tool_name": "calculate_supply_position", "decision_summary": "Coverage calculation required."}),
                event("tool_execution", {"tool_name": "calculate_supply_position", "tool_call_count": 1, "tool_seconds": 0.001}),
                event("agent_decision", {"action": "finish", "decision_summary": "Results available."}),
                event("agent_final", {"termination_reason": "final_answer"}),
            ],
            mode="procurement_agent",
        )
        text = build_diagnostics_clipboard_text(agent_snapshot)
        self.assertIn("Execution Summary", text)
        self.assertIn("Iterations: 2", text)
        self.assertIn("1. calculate_supply_position", text)
        self.assertIn("2. final_answer", text)
        self.assertIn("Termination reason: final_answer", text)
        self.assertNotIn("chain-of-thought", text.lower())

    def test_final_response_fallback_is_visible_in_diagnostics(self):
        text = build_diagnostics_clipboard_text(
            snapshot(
                [
                    event(
                        "final_response_validation",
                        {
                            "validation_status": "fallback",
                            "deterministic_fallback": True,
                            "validation_reasons": [
                                "short_deficit_presented_as_negative",
                                "unsupported_missing_information_claim",
                            ],
                        },
                    )
                ],
                mode="procurement_agent",
            )
        )
        self.assertIn("Final Response Validation", text)
        self.assertIn("Status: fallback", text)
        self.assertIn("Deterministic fallback: yes", text)
        self.assertIn("- short_deficit_presented_as_negative", text)

    def test_accepted_final_response_is_visible_in_diagnostics(self):
        text = build_diagnostics_clipboard_text(
            snapshot(
                [
                    event(
                        "final_response_validation",
                        {
                            "validation_status": "accepted",
                            "deterministic_fallback": False,
                            "validation_reasons": [],
                        },
                    )
                ],
                mode="procurement_planner",
            )
        )
        self.assertIn("Status: accepted", text)
        self.assertIn("Deterministic fallback: no", text)
        self.assertNotIn("Reasons:", text)

    def test_new_orchestration_modes_have_friendly_diagnostic_labels(self):
        planner_text = build_diagnostics_clipboard_text(
            snapshot([], mode="procurement_planner")
        )
        deterministic_text = build_diagnostics_clipboard_text(
            snapshot([], mode="procurement_deterministic")
        )
        self.assertIn("Mode: Planner Agent", planner_text)
        self.assertIn("Mode: Deterministic", deterministic_text)
        self.assertIn("Tool selection: application-controlled", deterministic_text)


if __name__ == "__main__":
    unittest.main()
