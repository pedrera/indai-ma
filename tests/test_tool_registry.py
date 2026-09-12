import unittest

from tool_registry import action_fingerprint, procurement_tool_registry


class ToolRegistryTests(unittest.TestCase):
    def test_procurement_registry_exposes_only_procurement_tools(self) -> None:
        registry = procurement_tool_registry()
        self.assertEqual(
            {item.name for item in registry.descriptors()},
            {
                "calculate_supply_position",
                "calculate_spot_exposure",
                "calculate_demand_scenario",
            },
        )

    def test_registry_rejects_tool_outside_scope(self) -> None:
        with self.assertRaises(ValueError):
            procurement_tool_registry().execute(
                "calculate_margin",
                {
                    "sales_price_eur_mwh": 60,
                    "supply_cost_eur_mwh": 40,
                    "volume_gwh": 10,
                },
            )

    def test_fingerprint_is_independent_of_argument_order(self) -> None:
        self.assertEqual(
            action_fingerprint("tool", {"a": 1, "b": 2}),
            action_fingerprint("tool", {"b": 2, "a": 1}),
        )


if __name__ == "__main__":
    unittest.main()
