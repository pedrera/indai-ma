import unittest

from agent_models import AgentDecision, AgentStatus
from procurement_agent import ProcurementAgent
from procurement_agent import ProviderDecisionModel
from procurement_deterministic import ProcurementDeterministicWorkflow
from procurement_common import extract_procurement_context
from procurement_planner import (
    AgentPlan,
    PlannedAction,
    ProcurementAgentPlanner,
    ProviderPlannerModel,
    parse_agent_plan,
    _plan_prompt,
)
from llm_client import LLMResponse, _collection_character_count


REFERENCE = (
    "Tenemos una demanda prevista de 120 GWh y 95 GWh de gas ya aprovisionado.\n"
    "El precio spot actual es de 42 €/MWh.\n"
    "Analiza nuestra posición de aprovisionamiento y el coste de cubrir cualquier déficit."
)


class FakeSynthesisModel:
    def synthesize(self, request, executions, timeout_seconds=None):
        return "Síntesis basada en resultados deterministas."


class FakePlannerModel(FakeSynthesisModel):
    def __init__(self, plan):
        self.plan = plan
        self.synthesize_calls = 0
        self.offered_tool_names = []

    def create_plan(self, goal, context, tools, timeout_seconds=None):
        self.offered_tool_names = [tool["name"] for tool in tools]
        return self.plan

    def synthesize(self, request, executions, timeout_seconds=None):
        self.synthesize_calls += 1
        return super().synthesize(request, executions, timeout_seconds)


class ScriptedDecisionModel:
    def __init__(self, decisions):
        self.decisions = iter(decisions)

    def decide(self, goal, state, tools, timeout_seconds=None):
        return next(self.decisions)


class CapturingProvider:
    provider_name = "test"
    model = "test-model"
    recorder = None

    def __init__(self, contents):
        self.contents = iter(contents)
        self.options = []

    def generate_response(self, messages, timeout_seconds=None, options=None):
        self.options.append(options)
        return LLMResponse(next(self.contents))


def reference_plan():
    return AgentPlan(
        actions=[
            PlannedAction(
                tool_name="calculate_supply_position",
                arguments={
                    "expected_demand_gwh": 120,
                    "contracted_supply_gwh": 95,
                },
            ),
            PlannedAction(
                tool_name="calculate_spot_exposure",
                arguments={
                    "expected_demand_gwh": 120,
                    "contracted_supply_gwh": 95,
                    "spot_price_eur_mwh": 42,
                },
            ),
        ]
    )


def corrected_reference_plan():
    return reference_plan()


def results_by_name(result):
    return {item["name"]: item["result"] for item in result.tool_executions}


class ProcurementStrategyTests(unittest.TestCase):
    def test_empty_tool_collection_has_zero_schema_characters(self):
        self.assertEqual(_collection_character_count([]), 0)

    def test_reference_input_has_no_implicit_demand_scenarios(self):
        context = extract_procurement_context(REFERENCE)
        self.assertEqual(context.scenario_variations, ())

    def test_planner_only_receives_tools_eligible_for_current_input(self):
        model = FakePlannerModel(corrected_reference_plan())
        ProcurementAgentPlanner(model).run(REFERENCE)
        self.assertNotIn("calculate_demand_scenario", model.offered_tool_names)
        self.assertIn("calculate_spot_exposure", model.offered_tool_names)

        no_spot_model = FakePlannerModel(
            AgentPlan(actions=[corrected_reference_plan().actions[0]])
        )
        ProcurementAgentPlanner(no_spot_model).run(
            "Tenemos demanda prevista de 120 GWh y 95 GWh aprovisionados."
        )
        self.assertNotIn(
            "calculate_spot_exposure", no_spot_model.offered_tool_names
        )

    def test_planner_prompt_forbids_spot_action_without_physical_short(self):
        context = extract_procurement_context(
            "Demanda 90 GWh, suministro 100 GWh y spot 42 EUR/MWh."
        )
        prompt = _plan_prompt("goal", context, [])
        self.assertIn("igual o inferior al suministro", prompt)
        self.assertIn("no incluyas calculate_spot_exposure", prompt)

    def test_structured_plan_is_parsed_and_validated(self):
        parsed = parse_agent_plan(reference_plan().model_dump_json())
        self.assertEqual(len(parsed.actions), 2)
        self.assertEqual(parsed.actions[0].tool_name, "calculate_supply_position")

    def test_plan_parser_accepts_json_surrounded_by_model_commentary(self):
        wrapped = (
            "<think>internal text that must not be used</think>\n"
            "Aquí está el plan:\n```json\n"
            + reference_plan().model_dump_json()
            + "\n```\nFin."
        )
        parsed = parse_agent_plan(wrapped)
        self.assertEqual(len(parsed.actions), 2)

    def test_plan_parser_does_not_accept_arbitrary_or_extra_fields(self):
        with self.assertRaisesRegex(ValueError, "valid AgentPlan"):
            parse_agent_plan('{"actions": [], "python": "run()"}')

    def test_provider_models_record_plan_decision_and_final_purposes(self):
        planner_provider = CapturingProvider([
            corrected_reference_plan().model_dump_json(),
            "Síntesis final",
        ])
        planner = ProcurementAgentPlanner(ProviderPlannerModel(planner_provider))
        planner.run(REFERENCE)
        self.assertEqual(
            [option.trace_purpose for option in planner_provider.options],
            ["agent_plan", "agent_final_synthesis"],
        )
        react_provider = CapturingProvider([
            '{"action":"finish","answer":"Final","decision_summary":"Complete"}'
        ])
        ProcurementAgent(ProviderDecisionModel(react_provider)).run(REFERENCE)
        self.assertEqual(
            react_provider.options[0].trace_purpose, "agent_decision"
        )
        self.assertGreater(
            react_provider.options[0].trace_tool_schema_character_count, 0
        )

    def test_planner_executes_valid_multi_tool_plan(self):
        result = ProcurementAgentPlanner(
            FakePlannerModel(corrected_reference_plan())
        ).run(REFERENCE)
        self.assertEqual(result.status, AgentStatus.COMPLETED)
        values = results_by_name(result)
        self.assertEqual(values["calculate_supply_position"]["position_gwh"], -25)
        self.assertEqual(values["calculate_spot_exposure"]["exposure_eur"], 1_050_000)

    def test_unknown_tool_rejects_entire_plan_without_execution(self):
        plan = AgentPlan(actions=[PlannedAction(tool_name="buy_gas_now", arguments={})])
        result = ProcurementAgentPlanner(FakePlannerModel(plan)).run(REFERENCE)
        self.assertEqual(result.status, AgentStatus.FAILED)
        self.assertEqual(result.tool_executions, ())

    def test_invalid_arguments_are_rejected(self):
        invalid = AgentPlan(actions=[PlannedAction(
            tool_name="calculate_supply_position",
            arguments={"expected_demand_gwh": 120, "wrong_supply": 95},
        )])
        result = ProcurementAgentPlanner(
            FakePlannerModel(invalid)
        ).run(REFERENCE)
        self.assertEqual(result.status, AgentStatus.FAILED)
        self.assertEqual(result.tool_executions, ())

    def test_duplicate_actions_are_rejected(self):
        action = corrected_reference_plan().actions[0]
        result = ProcurementAgentPlanner(
            FakePlannerModel(AgentPlan(actions=[action, action]))
        ).run(REFERENCE)
        self.assertEqual(result.termination_reason, "plan_validation_failed")

    def test_spot_exposure_requires_position_first(self):
        exposure_only = AgentPlan(actions=[corrected_reference_plan().actions[1]])
        result = ProcurementAgentPlanner(FakePlannerModel(exposure_only)).run(REFERENCE)
        self.assertEqual(result.termination_reason, "plan_validation_failed")
        self.assertEqual(result.tool_executions, ())

    def test_incomplete_short_plan_is_rejected_without_synthesis(self):
        model = FakePlannerModel(
            AgentPlan(actions=[corrected_reference_plan().actions[0]])
        )
        result = ProcurementAgentPlanner(model).run(REFERENCE)
        self.assertEqual(result.termination_reason, "plan_validation_failed")
        self.assertEqual(result.tool_executions, ())
        self.assertEqual(model.synthesize_calls, 0)

    def test_missing_required_execution_evidence_blocks_synthesis(self):
        context = extract_procurement_context(REFERENCE)
        executions = [
            {
                "name": "calculate_supply_position",
                "result": {"position_gwh": -25, "interpretation": "SHORT"},
            }
        ]
        with self.assertRaisesRegex(ValueError, "spot-exposure"):
            ProcurementAgentPlanner.validate_execution_evidence(
                context, executions
            )

    def test_maximum_plan_actions_is_enforced(self):
        action = corrected_reference_plan().actions[0]
        result = ProcurementAgentPlanner(
            FakePlannerModel(AgentPlan(actions=[action] * 6))
        ).run(REFERENCE)
        self.assertEqual(result.status, AgentStatus.FAILED)

    def test_all_modes_have_identical_reference_calculations(self):
        deterministic = ProcurementDeterministicWorkflow(FakeSynthesisModel()).run(
            REFERENCE
        )
        planner = ProcurementAgentPlanner(
            FakePlannerModel(corrected_reference_plan())
        ).run(REFERENCE)
        react = ProcurementAgent(
            ScriptedDecisionModel(
                [
                    AgentDecision("call_tool", "Position required", "calculate_supply_position", {"expected_demand_gwh": 120, "contracted_supply_gwh": 95}),
                    AgentDecision("call_tool", "Exposure required", "calculate_spot_exposure", {"expected_demand_gwh": 120, "contracted_supply_gwh": 95, "spot_price_eur_mwh": 42}),
                    AgentDecision("finish", "Complete", answer="Síntesis basada en resultados deterministas."),
                ]
            )
        ).run(REFERENCE)
        for result in (deterministic, planner, react):
            values = results_by_name(result)
            self.assertEqual(values["calculate_supply_position"]["position_gwh"], -25)
            self.assertEqual(values["calculate_supply_position"]["interpretation"], "SHORT")
            self.assertEqual(values["calculate_spot_exposure"]["short_position_gwh"], 25)
            self.assertEqual(values["calculate_spot_exposure"]["exposure_eur"], 1_050_000)
        self.assertEqual(
            [item["name"] for item in deterministic.tool_executions],
            ["calculate_supply_position", "calculate_spot_exposure"],
        )

    def test_missing_spot_never_creates_exposure(self):
        request = "Tenemos demanda prevista de 120 GWh y 95 GWh ya aprovisionados."
        plan = AgentPlan(actions=[corrected_reference_plan().actions[0]])
        for result in (
            ProcurementDeterministicWorkflow(FakeSynthesisModel()).run(request),
            ProcurementAgentPlanner(FakePlannerModel(plan)).run(request),
        ):
            values = results_by_name(result)
            self.assertEqual(values["calculate_supply_position"]["position_gwh"], -25)
            self.assertNotIn("calculate_spot_exposure", values)

    def test_long_and_balanced_skip_unnecessary_exposure(self):
        for demand, supply, interpretation in ((90, 100, "LONG"), (100, 100, "BALANCED")):
            request = f"Demanda prevista de {demand} GWh, {supply} GWh aprovisionados y spot 42 €/MWh."
            plan = AgentPlan(actions=[
                PlannedAction(tool_name="calculate_supply_position", arguments={"expected_demand_gwh": demand, "contracted_supply_gwh": supply}),
                PlannedAction(tool_name="calculate_spot_exposure", arguments={"expected_demand_gwh": demand, "contracted_supply_gwh": supply, "spot_price_eur_mwh": 42}),
            ])
            for result in (
                ProcurementDeterministicWorkflow(FakeSynthesisModel()).run(request),
                ProcurementAgentPlanner(FakePlannerModel(plan)).run(request),
            ):
                values = results_by_name(result)
                self.assertEqual(values["calculate_supply_position"]["interpretation"], interpretation)
                self.assertNotIn("calculate_spot_exposure", values)

    def test_scenario_tool_can_be_planned(self):
        request = REFERENCE + " Analiza también un escenario de demanda +10%."
        actions = [
            PlannedAction(tool_name="calculate_demand_scenario", arguments={"base_demand_gwh": 120, "variation_percent": 10}),
            *corrected_reference_plan().actions,
        ]
        result = ProcurementAgentPlanner(
            FakePlannerModel(AgentPlan(actions=actions))
        ).run(request)
        values = results_by_name(result)
        self.assertEqual(values["calculate_demand_scenario"]["scenario_demand_gwh"], 132)

    def test_requested_scenario_cannot_be_omitted_from_plan(self):
        request = REFERENCE + " Analiza también un escenario de demanda +10%."
        model = FakePlannerModel(corrected_reference_plan())
        result = ProcurementAgentPlanner(model).run(request)
        self.assertEqual(result.termination_reason, "plan_validation_failed")
        self.assertEqual(result.tool_executions, ())
        self.assertEqual(model.synthesize_calls, 0)


if __name__ == "__main__":
    unittest.main()
