import unittest

from take_or_pay import calculate_take_or_pay_projection, parse_take_or_pay_inputs


class TakeOrPayTests(unittest.TestCase):
    def test_projection_below_minimum(self):
        result = calculate_take_or_pay_projection(40.8, 30, 8)
        self.assertEqual(result.projected_annual_consumption_gwh, 38)
        self.assertEqual(result.projected_take_or_pay_deficit_gwh, 2.8)
        self.assertEqual(result.status, "BELOW_MINIMUM")

    def test_projection_exact_and_above(self):
        exact = calculate_take_or_pay_projection(40.8, 30, 10.8)
        above = calculate_take_or_pay_projection(40.8, 30, 12)
        self.assertEqual((exact.projected_take_or_pay_deficit_gwh, exact.status), (0, "AT_MINIMUM"))
        self.assertEqual((above.projected_annual_consumption_gwh, above.status), (42, "ABOVE_MINIMUM"))

    def test_zero_remaining_and_cumulative_above(self):
        zero = calculate_take_or_pay_projection(40.8, 30, 0)
        above = calculate_take_or_pay_projection(40.8, 42, 0)
        self.assertEqual((zero.projected_take_or_pay_deficit_gwh, zero.status), (10.8, "BELOW_MINIMUM"))
        self.assertEqual((above.projected_take_or_pay_deficit_gwh, above.status), (0, "ABOVE_MINIMUM"))

    def test_parser_distinguishes_accumulated_and_remaining(self):
        parsed = parse_take_or_pay_inputs(
            "El consumo acumulado es de 30 GWh y esperamos consumir otros 8 GWh hasta final de año."
        )
        self.assertEqual(parsed.cumulative_consumption_gwh, 30)
        self.assertEqual(parsed.remaining_forecast_consumption_gwh, 8)

    def test_parser_variants_and_missing_values(self):
        self.assertEqual(parse_take_or_pay_inputs("Hemos consumido 30 GWh").model_dump(),
                         {"cumulative_consumption_gwh": 30.0, "remaining_forecast_consumption_gwh": None})
        self.assertEqual(parse_take_or_pay_inputs("Previsión restante 8 GWh").model_dump(),
                         {"cumulative_consumption_gwh": None, "remaining_forecast_consumption_gwh": 8.0})
        self.assertEqual(parse_take_or_pay_inputs("Sin datos temporales").model_dump(),
                         {"cumulative_consumption_gwh": None, "remaining_forecast_consumption_gwh": None})

    def test_parser_does_not_create_monthly_demand_or_supply(self):
        parsed = parse_take_or_pay_inputs("El consumo acumulado es de 30 GWh y esperamos consumir otros 8 GWh")
        self.assertFalse(hasattr(parsed, "expected_demand_gwh"))
        self.assertFalse(hasattr(parsed, "supply_gwh"))
        self.assertFalse(hasattr(parsed, "forecast_demand_gwh"))

    def test_projection_has_no_economic_or_procurement_outputs(self):
        result = calculate_take_or_pay_projection(40.8, 30, 8)
        for field in ("penalty", "cost", "spot_exposure_eur", "position_gwh", "purchase_recommendation"):
            self.assertNotIn(field, type(result).model_fields)


if __name__ == "__main__":
    unittest.main()
