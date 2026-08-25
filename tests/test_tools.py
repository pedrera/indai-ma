import json
import math
import unittest

from tools import (
    ToolExecutionError,
    calculate_demand_scenario,
    calculate_margin,
    calculate_spot_exposure,
    calculate_supply_position,
    execute_tool_call,
    get_chat_completion_tools,
    get_responses_tools,
)


class SupplyPositionTests(unittest.TestCase):
    def test_short_position(self) -> None:
        self.assertEqual(calculate_supply_position(120, 95), -25)
        execution = execute_tool_call(
            "calculate_supply_position",
            '{"expected_demand_gwh": 120, "contracted_supply_gwh": 95}',
        )
        self.assertEqual(execution.result["interpretation"], "SHORT")

    def test_long_position(self) -> None:
        self.assertEqual(calculate_supply_position(95, 120), 25)
        execution = execute_tool_call(
            "calculate_supply_position",
            '{"expected_demand_gwh": 95, "contracted_supply_gwh": 120}',
        )
        self.assertEqual(execution.result["interpretation"], "LONG")

    def test_balanced_position(self) -> None:
        self.assertEqual(calculate_supply_position(100, 100), 0)
        execution = execute_tool_call(
            "calculate_supply_position",
            '{"expected_demand_gwh": 100, "contracted_supply_gwh": 100}',
        )
        self.assertEqual(execution.result["interpretation"], "BALANCED")


class DemandScenarioTests(unittest.TestCase):
    def test_increase(self) -> None:
        result = calculate_demand_scenario(120, 10)
        self.assertEqual(result["scenario_demand_gwh"], 132)
        self.assertEqual(result["base_demand_gwh"], 120)
        self.assertEqual(result["variation_percent"], 10)

    def test_reduction(self) -> None:
        result = calculate_demand_scenario(120, -10)
        self.assertEqual(result["scenario_demand_gwh"], 108)

    def test_zero_variation(self) -> None:
        result = calculate_demand_scenario(120, 0)
        self.assertEqual(result["scenario_demand_gwh"], 120)


class SpotExposureTests(unittest.TestCase):
    def test_short_exposure_and_gwh_conversion(self) -> None:
        result = calculate_spot_exposure(132, 95, 42)
        self.assertEqual(result["short_position_gwh"], 37)
        self.assertEqual(result["exposure_eur"], 1_554_000)

    def test_long_has_no_exposure(self) -> None:
        result = calculate_spot_exposure(90, 100, 42)
        self.assertEqual(result["short_position_gwh"], 0)
        self.assertEqual(result["exposure_eur"], 0)

    def test_balanced_has_no_exposure(self) -> None:
        result = calculate_spot_exposure(100, 100, 42)
        self.assertEqual(result["short_position_gwh"], 0)
        self.assertEqual(result["exposure_eur"], 0)


class MarginTests(unittest.TestCase):
    def test_positive_margin_and_gwh_conversion(self) -> None:
        result = calculate_margin(60, 45, 10)
        self.assertEqual(
            result,
            {
                "sales_price_eur_mwh": 60,
                "supply_cost_eur_mwh": 45,
                "volume_gwh": 10,
                "margin_eur_mwh": 15,
                "total_margin_eur": 150_000,
            },
        )

    def test_negative_margin(self) -> None:
        result = calculate_margin(40, 45, 10)
        self.assertEqual(result["margin_eur_mwh"], -5)
        self.assertEqual(result["total_margin_eur"], -50_000)

    def test_zero_margin(self) -> None:
        result = calculate_margin(45, 45, 10)
        self.assertEqual(result["margin_eur_mwh"], 0)
        self.assertEqual(result["total_margin_eur"], 0)


class ToolContractTests(unittest.TestCase):
    EXPECTED_TOOLS = {
        "calculate_supply_position",
        "calculate_demand_scenario",
        "calculate_spot_exposure",
        "calculate_margin",
    }

    def test_both_provider_catalogs_contain_four_tools(self) -> None:
        chat_names = {
            item["function"]["name"]
            for item in get_chat_completion_tools()
        }
        responses_names = {
            item["name"] for item in get_responses_tools()
        }
        self.assertEqual(chat_names, self.EXPECTED_TOOLS)
        self.assertEqual(responses_names, self.EXPECTED_TOOLS)

    def test_missing_and_extra_arguments_are_rejected(self) -> None:
        for arguments in (
            '{"base_demand_gwh": 120}',
            '{"base_demand_gwh": 120, "variation_percent": 10, "extra": 1}',
        ):
            with self.assertRaises(ToolExecutionError):
                execute_tool_call("calculate_demand_scenario", arguments)

    def test_boolean_nan_and_infinity_are_rejected(self) -> None:
        invalid_values = (True, math.nan, math.inf)
        for value in invalid_values:
            arguments = json.dumps(
                {
                    "base_demand_gwh": value,
                    "variation_percent": 10,
                }
            )
            with self.subTest(value=value):
                with self.assertRaises(ToolExecutionError):
                    execute_tool_call(
                        "calculate_demand_scenario", arguments
                    )

    def test_model_output_matches_structured_result(self) -> None:
        execution = execute_tool_call(
            "calculate_margin",
            '{"sales_price_eur_mwh": 60, "supply_cost_eur_mwh": 45, "volume_gwh": 10}',
        )
        self.assertEqual(
            json.loads(execution.model_output()), execution.result
        )


if __name__ == "__main__":
    unittest.main()
