import unittest

from contractual_analysis import (
    calculate_contractual_volume_impact,
    extract_contractual_volume_terms,
)


class ContractualVolumeAnalysisTests(unittest.TestCase):
    def test_contractual_excess_is_independent_from_supply_short(self) -> None:
        chunks = [
            {
                "chunk_id": "reference",
                "text": "El consumo mensual de referencia será de 4 GWh.",
            },
            {
                "chunk_id": "flexibility",
                "text": "Flexibilidad de consumo de ±15%.",
            },
            {
                "chunk_id": "price",
                "text": "El precio aplicable será Precio Spot + 4 €/MWh.",
            },
        ]
        terms = extract_contractual_volume_terms(chunks)
        impact = calculate_contractual_volume_impact(terms, 4.8, 42)

        self.assertEqual(terms.reference_volume_gwh, 4)
        self.assertEqual(terms.flexibility_percent, 15)
        self.assertEqual(terms.excess_surcharge_eur_mwh, 4)
        self.assertEqual(impact.contractual_max_gwh, 4.6)
        self.assertEqual(impact.contractual_excess_gwh, 0.2)
        self.assertEqual(impact.contractual_excess_price_eur_mwh, 46)
        self.assertEqual(
            terms.field_sources["flexibility_percent"], "flexibility"
        )

    def test_no_excess_when_forecast_is_within_flexibility(self) -> None:
        terms = extract_contractual_volume_terms(
            [
                {
                    "chunk_id": "terms",
                    "text": (
                        "Consumo mensual de referencia de 4 GWh y "
                        "flexibilidad del 15%."
                    ),
                }
            ]
        )
        impact = calculate_contractual_volume_impact(terms, 4.3, 42)
        self.assertEqual(impact.contractual_excess_gwh, 0)


if __name__ == "__main__":
    unittest.main()
