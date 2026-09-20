import unittest

from agent_models import AgentDecision, AgentPolicy, AgentStatus
from diagnostics import PerformanceRecorder
from procurement_agent import ProcurementAgent, parse_agent_decision


class ScriptedDecisionModel:
    def __init__(self, decisions):
        self.decisions = iter(decisions)
        self.states = []
        self.offered_tool_names = []

    def decide(self, goal, state, tools, timeout_seconds=None):
        self.states.append(state)
        self.offered_tool_names.append([tool["name"] for tool in tools])
        decision = next(self.decisions)
        if isinstance(decision, Exception):
            raise decision
        return decision


def call(name, arguments):
    return AgentDecision("call_tool", "Cálculo determinista necesario.", name, arguments)


def finish(answer="Análisis completado."):
    return AgentDecision("finish", "Los resultados necesarios están disponibles.", answer=answer)


class ProcurementAgentTests(unittest.TestCase):
    def test_missing_spot_is_structured_while_short_remains_calculable(self) -> None:
        result = ProcurementAgent(ScriptedDecisionModel([
            call("calculate_supply_position", {"expected_demand_gwh": 4.8, "contracted_supply_gwh": 4.3}),
            finish(),
        ])).run("Demanda 4.8 GWh. Tenemos 4.3 GWh aprovisionados. Posición.")
        self.assertEqual(result.missing_inputs, ("spot_price_eur_mwh",))
        self.assertEqual(result.tool_executions[0]["result"]["interpretation"], "SHORT")

    def test_short_position_then_spot_exposure(self) -> None:
        model = ScriptedDecisionModel(
            [
                call(
                    "calculate_supply_position",
                    {"expected_demand_gwh": 120, "contracted_supply_gwh": 95},
                ),
                call(
                    "calculate_spot_exposure",
                    {
                        "expected_demand_gwh": 120,
                        "contracted_supply_gwh": 95,
                        "spot_price_eur_mwh": 42,
                    },
                ),
                finish("Posición SHORT de 25 GWh y exposición de 1.050.000 EUR."),
            ]
        )
        recorder = PerformanceRecorder("agent-1", "test", "scripted", "procurement_agent")
        result = ProcurementAgent(model, recorder=recorder).run(
            "Gas natural: demanda prevista 120 GWh, suministro contratado 95 GWh "
            "y precio spot 42 EUR/MWh."
        )
        self.assertEqual(result.status, AgentStatus.COMPLETED)
        self.assertEqual(len(result.tool_executions), 2)
        self.assertEqual(result.tool_executions[0]["result"]["interpretation"], "SHORT")
        self.assertEqual(result.tool_executions[1]["result"]["exposure_eur"], 1_050_000)
        self.assertNotIn(
            "demand_variations_percent", model.states[0]["known_facts"]
        )
        self.assertNotIn(
            "calculate_demand_scenario", model.offered_tool_names[0]
        )
        self.assertEqual(model.states[0]["unresolved_inputs"], [])
        self.assertEqual(
            [event.stage for event in recorder.snapshot().events],
            [
                "agent_start",
                "agent_decision",
                "tool_execution",
                "agent_observation",
                "agent_decision",
                "tool_execution",
                "agent_observation",
                "agent_decision",
                "final_response_validation",
                "agent_final",
            ],
        )

    def test_duplicate_tool_call_is_not_executed_twice(self) -> None:
        repeated = call(
            "calculate_supply_position",
            {"expected_demand_gwh": 120, "contracted_supply_gwh": 95},
        )
        result = ProcurementAgent(
            ScriptedDecisionModel([repeated, repeated, finish()])
        ).run("demanda prevista 120 GWh y suministro contratado 95 GWh")
        self.assertEqual(len(result.tool_executions), 1)
        self.assertEqual(result.observations[-1].kind, "duplicate_tool_call")

    def test_hallucinated_tool_never_executes(self) -> None:
        result = ProcurementAgent(
            ScriptedDecisionModel([call("invent_price", {"value": 1}), finish()])
        ).run("Analiza la compra de gas")
        self.assertEqual(result.status, AgentStatus.COMPLETED)
        self.assertEqual(result.tool_executions, ())
        self.assertEqual(result.observations[0].kind, "tool_error")

    def test_decision_budget_stops_loop(self) -> None:
        decisions = [
            call(
                "calculate_supply_position",
                {"expected_demand_gwh": value, "contracted_supply_gwh": 95},
            )
            for value in (100, 110)
        ]
        result = ProcurementAgent(
            ScriptedDecisionModel(decisions),
            policy=AgentPolicy(max_decisions=2, max_tool_calls=3),
        ).run("Analiza la posición")
        self.assertEqual(result.status, AgentStatus.BUDGET_EXHAUSTED)
        self.assertEqual(result.termination_reason, "decision_budget_exhausted")

    def test_request_information_is_terminal(self) -> None:
        decision = AgentDecision(
            "request_information",
            "Falta el precio spot.",
            missing_fields=("spot_price_eur_mwh",),
            question="¿Cuál es el precio spot en EUR/MWh?",
        )
        result = ProcurementAgent(ScriptedDecisionModel([decision])).run(
            "Demanda 120 GWh y suministro contratado 95 GWh"
        )
        self.assertEqual(result.status, AgentStatus.NEEDS_INPUT)
        self.assertIn("precio spot", result.content)

    def test_explicit_scenario_remains_available_to_react(self) -> None:
        model = ScriptedDecisionModel([finish()])
        ProcurementAgent(model).run(
            "Demanda 120 GWh, suministro 95 GWh, spot 42 EUR/MWh y escenario +10%."
        )
        self.assertEqual(
            model.states[0]["known_facts"]["demand_variations_percent"],
            [10.0],
        )
        self.assertIn(
            "calculate_demand_scenario", model.offered_tool_names[0]
        )

    def test_long_position_skips_react_spot_action(self) -> None:
        model = ScriptedDecisionModel(
            [
                call(
                    "calculate_supply_position",
                    {"expected_demand_gwh": 90, "contracted_supply_gwh": 100},
                ),
                call(
                    "calculate_spot_exposure",
                    {
                        "expected_demand_gwh": 90,
                        "contracted_supply_gwh": 100,
                        "spot_price_eur_mwh": 42,
                    },
                ),
                finish("La posición es LONG en 10 GWh."),
            ]
        )
        result = ProcurementAgent(model).run(
            "Demanda 90 GWh, suministro 100 GWh y spot 42 EUR/MWh."
        )
        self.assertEqual(
            [item["name"] for item in result.tool_executions],
            ["calculate_supply_position"],
        )
        self.assertEqual(result.observations[-1].kind, "tool_skipped")
        self.assertIn("non-SHORT", result.observations[-1].message)

    def test_react_cannot_finish_before_requested_scenario_is_observed(self) -> None:
        model = ScriptedDecisionModel(
            [
                call(
                    "calculate_supply_position",
                    {"expected_demand_gwh": 120, "contracted_supply_gwh": 95},
                ),
                call(
                    "calculate_spot_exposure",
                    {
                        "expected_demand_gwh": 120,
                        "contracted_supply_gwh": 95,
                        "spot_price_eur_mwh": 42,
                    },
                ),
                finish("Respuesta prematura con escenario inventado."),
                call(
                    "calculate_demand_scenario",
                    {"base_demand_gwh": 120, "variation_percent": 10},
                ),
                finish("SHORT 25 GWh; 1.050.000 €; escenario +10%: 132 GWh."),
            ]
        )
        result = ProcurementAgent(model).run(
            "Demanda 120 GWh, suministro 95 GWh, spot 42 EUR/MWh y escenario +10%."
        )
        self.assertEqual(result.status, AgentStatus.COMPLETED)
        self.assertEqual(result.decision_count, 5)
        self.assertEqual(
            [item["name"] for item in result.tool_executions],
            [
                "calculate_supply_position",
                "calculate_spot_exposure",
                "calculate_demand_scenario",
            ],
        )
        self.assertTrue(
            any(item.kind == "incomplete_analysis" for item in result.observations)
        )

    def test_invalid_decisions_have_a_bounded_retry(self) -> None:
        result = ProcurementAgent(
            ScriptedDecisionModel([ValueError("bad"), ValueError("bad again")])
        ).run("Analiza")
        self.assertEqual(result.status, AgentStatus.FAILED)
        self.assertEqual(result.decision_count, 2)

    def test_parser_accepts_json_fence_without_exposing_reasoning(self) -> None:
        decision = parse_agent_decision(
            '```json\n{"action":"finish","answer":"Listo",'
            '"decision_summary":"Cálculos disponibles."}\n```'
        )
        self.assertEqual(decision.action, "finish")
        self.assertEqual(decision.answer, "Listo")


if __name__ == "__main__":
    unittest.main()
