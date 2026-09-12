import unittest

from procurement_response import validate_procurement_final_response


POSITION = {
    "name": "calculate_supply_position",
    "result": {"interpretation": "SHORT", "position_gwh": -25.0},
}
EXPOSURE = {
    "name": "calculate_spot_exposure",
    "result": {"exposure_eur": 1_050_000.0},
}
LONG_POSITION = {
    "name": "calculate_supply_position",
    "result": {"interpretation": "LONG", "position_gwh": 10.0},
}
BALANCED_POSITION = {
    "name": "calculate_supply_position",
    "result": {"interpretation": "BALANCED", "position_gwh": 0.0},
}


class ProcurementResponseValidationTests(unittest.TestCase):
    def test_valid_short_response_is_preserved(self):
        content = "La posición de aprovisionamiento es SHORT en 25 GWh."
        result = validate_procurement_final_response(content, [POSITION])
        self.assertTrue(result.is_valid)
        self.assertEqual(result.content, content)

    def test_negative_short_and_unneeded_tools_trigger_canonical_fallback(self):
        content = (
            "La posición es SHORT en -25 GWh. Se requiere evaluar "
            "herramientas adicionales."
        )
        result = validate_procurement_final_response(content, [POSITION])
        self.assertTrue(result.fallback_used)
        self.assertEqual(
            result.content,
            "La posición de aprovisionamiento es SHORT en 25 GWh.",
        )
        self.assertIn("short_deficit_presented_as_negative", result.reasons)
        self.assertIn("unsupported_missing_information_claim", result.reasons)

    def test_exposure_claim_requires_tool_result(self):
        result = validate_procurement_final_response(
            "La posición es SHORT. El coste para cubrir es de 1.050.000 €.",
            [POSITION],
        )
        self.assertTrue(result.fallback_used)
        self.assertNotIn("€", result.content)

    def test_explicitly_negated_exposure_without_tool_is_accepted(self):
        for content in (
            "La posición es LONG en 10 GWh. No hay exposición económica.",
            "La posición es LONG en 10 GWh. No se calcula la exposición económica.",
            "La posición es LONG en 10 GWh. No se requiere calcular la exposición económica.",
            "La posición es LONG en 10 GWh. No es necesario evaluar la exposición económica.",
            "La posición es LONG en 10 GWh, sin coste para cubrir un déficit.",
        ):
            with self.subTest(content=content):
                result = validate_procurement_final_response(
                    content, [LONG_POSITION]
                )
                self.assertTrue(result.is_valid)
                self.assertFalse(result.fallback_used)

    def test_positive_exposure_wording_without_tool_is_still_rejected(self):
        result = validate_procurement_final_response(
            "La posición es LONG en 10 GWh, con exposición económica LONG.",
            [LONG_POSITION],
        )
        self.assertTrue(result.fallback_used)

        zero_spot = validate_procurement_final_response(
            "La posición es LONG en 10 GWh. La exposición en el mercado spot es cero.",
            [LONG_POSITION],
        )
        self.assertTrue(zero_spot.fallback_used)

    def test_localized_balanced_interpretation_and_unit_price_are_accepted(self):
        content = (
            "La posición está equilibrada en 0 GWh. No hay exposición al "
            "mercado spot aunque el precio sea 42 €/MWh."
        )
        result = validate_procurement_final_response(
            content, [BALANCED_POSITION]
        )
        self.assertTrue(result.is_valid)
        self.assertFalse(result.fallback_used)

    def test_localized_position_terms_are_accepted(self):
        short = validate_procurement_final_response(
            "Existe una posición corta de 25 GWh.", [POSITION]
        )
        long = validate_procurement_final_response(
            "Existe un exceso de aprovisionamiento de 10 GWh.", [LONG_POSITION]
        )
        self.assertTrue(short.is_valid)
        self.assertTrue(long.is_valid)

    def test_canonical_fallback_uses_authoritative_exposure(self):
        result = validate_procurement_final_response(
            "Respuesta contradictoria sin posición.", [POSITION, EXPOSURE]
        )
        self.assertEqual(
            result.content,
            "La posición de aprovisionamiento es SHORT en 25 GWh. "
            "El coste para cubrir el déficit a precio spot es de 1.050.000,00 €.",
        )

    def test_unbacked_second_economic_amount_triggers_fallback(self):
        scenario = {
            "name": "calculate_demand_scenario",
            "result": {"variation_percent": 10.0, "scenario_demand_gwh": 132.0},
        }
        result = validate_procurement_final_response(
            "SHORT 25 GWh. Coste 1.050.000 €. Escenario +10%: exposición 1.428.000 €.",
            [POSITION, EXPOSURE, scenario],
        )
        self.assertTrue(result.fallback_used)
        self.assertIn("unsupported_economic_amount", result.reasons)
        self.assertNotIn("1.428.000", result.content)
        self.assertIn("132 GWh", result.content)


if __name__ == "__main__":
    unittest.main()
