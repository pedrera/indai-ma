import json
import unittest

from diagnostics import PerformanceRecorder
from gas_analysis import (
    GasAnalysisError,
    extract_demand_scenarios,
    extract_gas_positions,
    parse_scenario_analysis,
    parse_scenario_input,
    prepare_gas_analysis,
)
from gas_type_resolution import resolve_gas_type


def _recorder(operation_id: str) -> PerformanceRecorder:
    return PerformanceRecorder(
        operation_id, "lmstudio", "test-model", "gas_analysis"
    )


def _case(
    supply: float = 95,
    sales_price: float = 45,
    supply_cost: float = 32,
    spot_price: float = 42,
) -> str:
    return (
        "Gas natural con demanda prevista de 120 GWh y suministro "
        f"contratado de {supply:g} GWh. Precio de venta "
        f"{sales_price:g} EUR/MWh, coste de suministro "
        f"{supply_cost:g} EUR/MWh y precio spot {spot_price:g} EUR/MWh."
    )


def _natural_language_case() -> str:
    return (
        "La demanda prevista es de 120 GWh de gas natural y disponemos de "
        "95 GWh de suministro contratado. El coste medio de suministro es "
        "32 €/MWh, el precio medio de venta es 45 €/MWh y el precio spot "
        "es 42 €/MWh. Compara el escenario base, uno en el que la demanda "
        "aumenta un 10% y otro en el que aumenta un 20%."
    )


class ScenarioOrchestrationTests(unittest.TestCase):
    def test_forecast_consumption_is_detected_as_demand(self) -> None:
        result = parse_scenario_input(
            "El Hospital prevé consumir 4,8 GWh."
        )
        self.assertEqual(result.base_demand_candidates, [4.8])

    def test_demand_language_variants(self) -> None:
        cases = (
            "prevé consumir 4,8 GWh",
            "consumo previsto de 4,8 GWh",
            "demanda prevista de 4,8 GWh",
            "esperamos un consumo de 4,8 GWh",
        )
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(
                    parse_scenario_input(text).base_demand_candidates,
                    [4.8],
                )

    def test_provisioned_volume_is_detected_as_supply(self) -> None:
        result = parse_scenario_input(
            "Tenemos 4,3 GWh de gas ya aprovisionado."
        )
        self.assertEqual(result.contracted_supply_candidates, [4.3])

    def test_supply_language_variants(self) -> None:
        cases = (
            "tenemos 4,3 GWh ya aprovisionados",
            "4,3 GWh de gas ya aprovisionado",
            "disponemos de 4,3 GWh aprovisionados",
            "tenemos contratados 4,3 GWh",
            "suministro contratado de 4,3 GWh",
        )
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(
                    parse_scenario_input(text).contracted_supply_candidates,
                    [4.3],
                )

    def test_dot_decimals_keep_demand_and_supply_associated(self) -> None:
        result = parse_scenario_input(
            "El Hospital prevé consumir 4.8 GWh y tenemos 4.3 GWh "
            "aprovisionados."
        )
        self.assertEqual(result.base_demand_candidates, [4.8])
        self.assertEqual(result.contracted_supply_candidates, [4.3])

    def test_rag_gas_resolution_combines_with_quantitative_values(self) -> None:
        text = """El Hospital Costa Sur prevé consumir 4,8 GWh el próximo mes.
Tenemos 4,3 GWh de gas ya aprovisionado para este cliente y el precio actual
del mercado spot es de 42 €/MWh.

Utilizando las condiciones de su contrato, analiza nuestra posición de
aprovisionamiento, la flexibilidad contractual, el volumen que tendremos
que cubrir y el impacto económico."""
        resolution = resolve_gas_type(
            text,
            ["CONTRATO MARCO DE SUMINISTRO DE GAS NATURAL"],
        )
        result = parse_scenario_input(text, resolution)
        self.assertEqual(result.detected_gas_types, ["natural_gas"])
        self.assertEqual(result.gas_type_source, "rag")
        self.assertEqual(result.base_demand_candidates, [4.8])
        self.assertEqual(result.contracted_supply_candidates, [4.3])
        self.assertEqual(result.spot_price_candidates, [42])

    def test_exact_multiline_prompt_has_diagnostic_parse_result(self) -> None:
        text = """Somos una empresa mayorista de gases que suministra gas natural a clientes
industriales y sanitarios.

Para la próxima semana tenemos una demanda prevista de 120 GWh de gas natural
y disponemos de 95 GWh de suministro contratado.

El coste medio de nuestro suministro contratado es de 32 €/MWh y el precio
medio de venta a nuestros clientes es de 45 €/MWh.

El precio actual del gas en el mercado spot es de 42 €/MWh.

Quiero analizar tres escenarios de demanda:
- escenario base
- demanda +10%
- demanda +20%."""
        result = parse_scenario_input(text)
        self.assertEqual(result.detected_gas_types, ["natural_gas"])
        self.assertEqual(result.base_demand_candidates, [120])
        self.assertEqual(result.contracted_supply_candidates, [95])
        self.assertEqual(result.supply_cost_candidates, [32])
        self.assertEqual(result.sales_price_candidates, [45])
        self.assertEqual(result.spot_price_candidates, [42])
        self.assertEqual(result.scenario_variations, [0, 10, 20])
        self.assertEqual(result.missing_fields, [])
        self.assertEqual(result.ambiguities, [])

        recorder = _recorder("exact-diagnostic-prompt")
        prepared = prepare_gas_analysis(text, recorder)
        self.assertEqual(
            [item.demand_variation_percent for item in prepared.scenarios],
            [0, 10, 20],
        )
        input_event = next(
            event
            for event in recorder.snapshot().events
            if event.stage == "input_parsing"
        )
        self.assertEqual(
            input_event.metadata["parsing_result"], result.as_dict()
        )

    def test_natural_language_prompt_extracts_explicit_scenarios(self) -> None:
        prepared = prepare_gas_analysis(
            _natural_language_case(), _recorder("natural-language")
        )
        self.assertEqual(
            [item.demand_variation_percent for item in prepared.scenarios],
            [0, 10, 20],
        )
        self.assertEqual(
            [item.demand_gwh for item in prepared.scenarios],
            [120, 132, 144],
        )
        first_tools = prepared.tool_executions[:4]
        self.assertEqual(
            first_tools[1]["arguments"]["contracted_supply_gwh"], 95
        )
        self.assertEqual(
            first_tools[2]["arguments"]["spot_price_eur_mwh"], 42
        )
        self.assertEqual(
            first_tools[3]["arguments"]["supply_cost_eur_mwh"], 32
        )
        self.assertEqual(
            first_tools[3]["arguments"]["sales_price_eur_mwh"], 45
        )

    def test_natural_language_fields_can_appear_in_different_order(self) -> None:
        text = (
            "El precio spot es 42 €/MWh. Para gas natural, el precio medio "
            "de venta es 45 €/MWh y hay 95 GWh de suministro contratado. "
            "La demanda prevista de 120 GWh de gas natural tiene un coste "
            "medio de suministro de 32 €/MWh. Escenario base; la demanda "
            "aumenta un 20% y aumenta un 10%."
        )
        prepared = prepare_gas_analysis(text, _recorder("different-order"))
        self.assertEqual(
            [item.demand_variation_percent for item in prepared.scenarios],
            [0, 20, 10],
        )
        self.assertTrue(
            all(item.spot_price_eur_mwh == 42 for item in prepared.scenarios)
        )

    def test_prices_accept_decimal_comma(self) -> None:
        text = _natural_language_case().replace(
            "32 €/MWh", "32,5 €/MWh"
        ).replace("45 €/MWh", "45,5 €/MWh").replace(
            "42 €/MWh", "42,25 €/MWh"
        )
        prepared = prepare_gas_analysis(text, _recorder("decimal-comma"))
        margin_call = prepared.tool_executions[3]
        self.assertEqual(margin_call["arguments"]["supply_cost_eur_mwh"], 32.5)
        self.assertEqual(margin_call["arguments"]["sales_price_eur_mwh"], 45.5)
        self.assertEqual(prepared.scenarios[0].spot_price_eur_mwh, 42.25)

    def test_missing_required_value_is_rejected_before_tools(self) -> None:
        text = _natural_language_case().replace(
            " y el precio spot es 42 €/MWh", ""
        )
        recorder = _recorder("missing-spot")
        with self.assertRaisesRegex(GasAnalysisError, "precio spot"):
            prepare_gas_analysis(text, recorder)
        input_event = next(
            event
            for event in recorder.snapshot().events
            if event.stage == "input_parsing"
        )
        self.assertIn(
            "spot_price_eur_mwh",
            input_event.metadata["parsing_result"]["missing_fields"],
        )

    def test_multiple_demand_candidates_have_specific_error(self) -> None:
        text = _natural_language_case().replace(
            "La demanda prevista es de 120 GWh de gas natural",
            "La demanda prevista es de 120 GWh y la demanda esperada es de "
            "130 GWh de gas natural",
        )
        result = parse_scenario_input(text)
        self.assertEqual(result.base_demand_candidates, [120, 130])
        self.assertIn("base_demand_gwh", result.ambiguities)
        recorder = _recorder("ambiguous-demand")
        with self.assertRaisesRegex(
            GasAnalysisError,
            r"varias demandas posibles: 120 GWh, 130 GWh",
        ):
            prepare_gas_analysis(text, recorder)
        self.assertFalse(
            [
                event
                for event in recorder.snapshot().events
                if event.stage == "tool_execution"
            ]
        )

    def test_explicit_scenarios_replace_defaults(self) -> None:
        text = _case() + " Escenario base, aumenta un 5% y aumenta un 15%."
        self.assertEqual(
            [value for _, value in extract_demand_scenarios(text)],
            [0, 5, 15],
        )

    def test_base_plus_ten_and_plus_twenty(self) -> None:
        prepared = prepare_gas_analysis(_case(), _recorder("scenarios"))
        self.assertEqual(
            [scenario.demand_gwh for scenario in prepared.scenarios],
            [120, 132, 144],
        )
        self.assertEqual(
            [scenario.supply_position_gwh for scenario in prepared.scenarios],
            [-25, -37, -49],
        )
        self.assertEqual(
            [scenario.short_position_gwh for scenario in prepared.scenarios],
            [25, 37, 49],
        )
        self.assertEqual(len(prepared.tool_executions), 12)

    def test_short_exposure_and_positive_margin(self) -> None:
        scenarios = prepare_gas_analysis(
            _case(), _recorder("short")
        ).scenarios
        self.assertEqual(scenarios[1].spot_exposure_eur, 1_554_000)
        self.assertEqual(scenarios[1].estimated_margin_eur, 1_346_000)

    def test_long_position_has_zero_exposure(self) -> None:
        scenarios = prepare_gas_analysis(
            _case(supply=150), _recorder("long")
        ).scenarios
        self.assertTrue(
            all(item.supply_position_gwh > 0 for item in scenarios)
        )
        self.assertTrue(
            all(item.short_position_gwh == 0 for item in scenarios)
        )
        self.assertTrue(
            all(item.spot_exposure_eur == 0 for item in scenarios)
        )

    def test_negative_margin(self) -> None:
        scenarios = prepare_gas_analysis(
            _case(sales_price=25), _recorder("negative")
        ).scenarios
        self.assertTrue(
            all(item.estimated_margin_eur < 0 for item in scenarios)
        )

    def test_events_include_scenario_and_aggregate_to_twelve(self) -> None:
        recorder = _recorder("events")
        prepare_gas_analysis(_case(), recorder)
        events = [
            event
            for event in recorder.snapshot().events
            if event.stage == "tool_execution"
        ]
        self.assertEqual(len(events), 12)
        self.assertTrue(
            all(event.metadata.get("scenario_name") for event in events)
        )

    def test_ambiguous_gas_association_is_rejected(self) -> None:
        recorder = _recorder("ambiguous")
        text = (
            "Gas natural y GNL con demanda prevista de 120 GWh y suministro "
            "contratado de 95 GWh. Precio de venta 45 EUR/MWh, coste de "
            "suministro 32 EUR/MWh y spot 42 EUR/MWh."
        )
        with self.assertRaises(GasAnalysisError):
            prepare_gas_analysis(text, recorder)
        self.assertFalse(
            [
                event
                for event in recorder.snapshot().events
                if event.stage == "tool_execution"
            ]
        )

    def test_variation_does_not_remove_unambiguous_multigas_positions(self) -> None:
        text = (
            "Gas natural con demanda prevista de 120 GWh y suministro "
            "contratado de 95 GWh. GNL con demanda prevista de 40 GWh y "
            "suministro contratado de 45 GWh. Escenario +10 %."
        )
        self.assertEqual(len(extract_gas_positions(text)), 2)

    def test_llm_interpretation_cannot_replace_numeric_scenarios(self) -> None:
        recorder = _recorder("interpretation")
        prepared = prepare_gas_analysis(_case(), recorder)
        model_output = json.dumps(
            {
                "summary": "Resumen",
                "key_risks": ["Riesgo"],
                "recommendation": "Recomendación",
                "scenarios": [{"demand_gwh": 999999}],
            }
        )
        analysis = parse_scenario_analysis(
            model_output, prepared.scenarios, recorder
        )
        self.assertEqual(
            [item.demand_gwh for item in analysis.scenarios],
            [120, 132, 144],
        )


if __name__ == "__main__":
    unittest.main()
