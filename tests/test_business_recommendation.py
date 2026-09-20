import unittest
from types import SimpleNamespace

from business_recommendation import compose_business_recommendation
from take_or_pay import calculate_take_or_pay_projection


def specialist(name, result):
    return SimpleNamespace(agent_name=name, result=result)


def procurement(position="SHORT", amount=0.5, exposure=21000):
    executions = [{"name": "calculate_supply_position", "result": {
        "interpretation": position, "position_gwh": -amount if position == "SHORT" else amount,
        "short_position_gwh": amount if position == "SHORT" else 0,
    }}]
    if exposure is not None:
        executions.append({"name": "calculate_spot_exposure", "result": {"exposure_eur": exposure}})
    return SimpleNamespace(tool_executions=executions)


def commercial_range(demand, minimum=3.4, maximum=4.6, excess=0):
    return SimpleNamespace(calculations=[SimpleNamespace(result={
        "forecast_demand_gwh": demand, "contractual_min_gwh": minimum,
        "contractual_max_gwh": maximum, "contractual_excess_gwh": excess,
    }, inputs={})])


class BusinessRecommendationTests(unittest.TestCase):
    def test_hospital_composes_structured_results_without_recalculation(self):
        commercial = SimpleNamespace(calculations=[SimpleNamespace(result={
            "contractual_excess_gwh": 0.2,
            "contractual_excess_price_eur_mwh": 46,
        }, inputs={"spot_price_eur_mwh": 42, "excess_surcharge_eur_mwh": 4})])
        risk = SimpleNamespace(base_scenario=SimpleNamespace(
            interpretation="SHORT", short_position_gwh=0.5), stress_scenarios=[])
        result = SimpleNamespace(specialist_results=[
            specialist("CommercialAgent", commercial),
            specialist("ProcurementAgent", procurement()),
            specialist("RiskAgent", risk),
        ])
        recommendation = compose_business_recommendation(result)
        self.assertTrue(recommendation.is_complete)
        self.assertIn("cubrir", recommendation.action.lower())
        self.assertIn("0.2", recommendation.contractual_implication)
        self.assertIn("+4 EUR/MWh", recommendation.contractual_implication)
        self.assertIn("46 EUR/MWh", recommendation.contractual_implication)
        self.assertNotIn("Spot + 46", recommendation.contractual_implication)
        self.assertIn("0.5", recommendation.operational_implication)
        self.assertIn("21000", dict(recommendation.supporting_metrics)["Exposición spot"])
        self.assertTrue(any("diferentes" in item for item in recommendation.rationale))

    def test_balanced_and_long_do_not_recommend_short_coverage(self):
        for interpretation in ("BALANCED", "LONG"):
            result = SimpleNamespace(specialist_results=[
                specialist("ProcurementAgent", procurement(interpretation, 0, None))
            ])
            recommendation = compose_business_recommendation(result)
            self.assertNotIn("cubrir el SHORT", recommendation.action)

    def test_long_in_range_is_explicit_and_complete(self):
        result = SimpleNamespace(specialist_results=[
            specialist("CommercialAgent", commercial_range(3.6)),
            specialist("ProcurementAgent", procurement("LONG", .7, None)),
        ])
        recommendation = compose_business_recommendation(result)
        self.assertTrue(recommendation.is_complete)
        self.assertEqual(recommendation.action, "Revisar las opciones de gestión del excedente operativo.")
        self.assertIn("3.6", recommendation.contractual_implication)
        self.assertIn("3.4–4.6", recommendation.contractual_implication)
        self.assertIn("LONG", recommendation.operational_implication)
        self.assertIn("0.7", recommendation.operational_implication)
        for forbidden in ("vender", "revender", "almacenar", "cancelar", "beneficio", "pérdida"):
            self.assertNotIn(forbidden, recommendation.action.lower() + " " + recommendation.rationale[0].lower())

    def test_long_range_boundaries_and_below_range_do_not_infer_take_or_pay(self):
        for demand, expected in ((3.4, "dentro del rango"), (4.6, "dentro del rango"),
                                 (3.2, "por debajo del rango")):
            with self.subTest(demand=demand):
                result = SimpleNamespace(specialist_results=[
                    specialist("CommercialAgent", commercial_range(demand)),
                    specialist("ProcurementAgent", procurement("LONG", 4.3 - demand, None)),
                ])
                recommendation = compose_business_recommendation(result)
                self.assertIn(expected, recommendation.contractual_implication)
                self.assertNotIn("take-or-pay", recommendation.contractual_implication.lower())

    def test_missing_results_are_incomplete_and_content_is_not_parsed(self):
        result = SimpleNamespace(specialist_results=[
            specialist("ProcurementAgent", SimpleNamespace(
                content="SHORT 999 GWh", tool_executions=[]))
        ])
        recommendation = compose_business_recommendation(result)
        self.assertFalse(recommendation.is_complete)
        self.assertTrue(recommendation.warnings)
        self.assertNotIn("999", " ".join(value for pair in recommendation.supporting_metrics for value in pair))

    def test_price_stress_uses_structured_risk_values_without_recalculation(self):
        commercial = SimpleNamespace(calculations=[SimpleNamespace(result={
            "contractual_excess_gwh": 0.2, "contractual_excess_price_eur_mwh": 46,
        }, inputs={"excess_surcharge_eur_mwh": 4})])
        base = SimpleNamespace(scenario_type="BASE", short_position_gwh=0.5,
                              interpretation="SHORT", spot_price_eur_mwh=42,
                              spot_exposure_eur=21000)
        price = SimpleNamespace(scenario_type="PRICE", stress_percent=20,
                                demand_gwh=4.8, spot_price_eur_mwh=50.4,
                                spot_exposure_eur=25200)
        delta = SimpleNamespace(scenario_name="Precio spot +20 %", scenario_type="PRICE",
                                stress_percent=20, exposure_change_eur=4200)
        risk = SimpleNamespace(base_scenario=base, stress_scenarios=[price], deltas=[delta], status="completed")
        result = SimpleNamespace(specialist_results=[specialist("CommercialAgent", commercial),
                                                     specialist("ProcurementAgent", procurement()),
                                                     specialist("RiskAgent", risk)])
        recommendation = compose_business_recommendation(result)
        self.assertTrue(recommendation.is_complete)
        self.assertEqual(recommendation.action, "Cubrir el SHORT operativo.")
        for value in ("20", "42", "50.4", "21,000", "25,200", "+4,200"):
            self.assertIn(value, recommendation.risk_implication)
        self.assertIn("0.2", recommendation.contractual_implication)
        self.assertIn("+4 EUR/MWh", recommendation.contractual_implication)
        self.assertIn("46 EUR/MWh", recommendation.contractual_implication)

    def test_demand_and_price_stress_remain_separate(self):
        base = SimpleNamespace(scenario_type="BASE", short_position_gwh=0.5, interpretation="SHORT",
                               spot_price_eur_mwh=42, spot_exposure_eur=21000)
        demand = SimpleNamespace(scenario_type="DEMAND", stress_percent=10, demand_gwh=5.28)
        price = SimpleNamespace(scenario_type="PRICE", stress_percent=20, demand_gwh=4.8,
                                spot_price_eur_mwh=50.4, spot_exposure_eur=25200)
        d1 = SimpleNamespace(scenario_name="Demanda +10 %", exposure_change_eur=20160)
        d2 = SimpleNamespace(scenario_name="Precio spot +20 %", exposure_change_eur=4200)
        risk = SimpleNamespace(base_scenario=base, stress_scenarios=[demand, price], deltas=[d1, d2], status="completed")
        result = SimpleNamespace(specialist_results=[specialist("RiskAgent", risk)])
        recommendation = compose_business_recommendation(result)
        self.assertIn("demanda +10%", recommendation.risk_implication)
        self.assertIn("spot del 20%", recommendation.risk_implication)

    def test_price_stress_without_exposure_is_incomplete(self):
        base = SimpleNamespace(scenario_type="BASE", short_position_gwh=0.5, interpretation="SHORT",
                               spot_price_eur_mwh=None, spot_exposure_eur=None)
        price = SimpleNamespace(scenario_type="PRICE", stress_percent=20, demand_gwh=4.8,
                                spot_price_eur_mwh=None, spot_exposure_eur=None)
        risk = SimpleNamespace(base_scenario=base, stress_scenarios=[price], deltas=[], status="partial")
        recommendation = compose_business_recommendation(SimpleNamespace(
            specialist_results=[specialist("RiskAgent", risk)]))
        self.assertFalse(recommendation.is_complete)
        self.assertTrue(recommendation.warnings)

    def test_take_or_pay_projection_is_composed_without_recalculation_or_procurement_mix(self):
        projection = calculate_take_or_pay_projection(40.8, 30, 8)
        commercial = SimpleNamespace(calculations=[], contract_facts=[], take_or_pay_projection=projection)
        result = SimpleNamespace(specialist_results=[specialist("CommercialAgent", commercial)])
        recommendation = compose_business_recommendation(result)
        self.assertTrue(recommendation.is_complete)
        self.assertIn("38", recommendation.contractual_implication)
        self.assertIn("2.8", recommendation.contractual_implication)
        self.assertIn("40.8", recommendation.contractual_implication)
        self.assertNotIn("comprar", recommendation.action.lower())
        self.assertNotIn("SHORT", " ".join(recommendation.rationale))

    def test_actions_are_structured_ordered_and_keep_domains_separate(self):
        projection = calculate_take_or_pay_projection(40.8, 30, 8)
        commercial = SimpleNamespace(calculations=[SimpleNamespace(result={
            "contractual_excess_gwh": 0.2,
        }, inputs={})], contract_facts=[], take_or_pay_projection=projection)
        base = SimpleNamespace(scenario_type="BASE", short_position_gwh=0.5,
                               interpretation="SHORT", spot_price_eur_mwh=42,
                               spot_exposure_eur=21000)
        price = SimpleNamespace(scenario_type="PRICE", stress_percent=20,
                                demand_gwh=4.8, spot_price_eur_mwh=50.4,
                                spot_exposure_eur=25200)
        risk = SimpleNamespace(base_scenario=base, stress_scenarios=[price],
                               deltas=[SimpleNamespace(scenario_name="Precio spot +20 %",
                                                       scenario_type="PRICE",
                                                       exposure_change_eur=4200)])
        recommendation = compose_business_recommendation(SimpleNamespace(
            specialist_results=[specialist("CommercialAgent", commercial),
                                 specialist("ProcurementAgent", procurement()),
                                 specialist("RiskAgent", risk)]))
        self.assertEqual([item.category for item in recommendation.actions],
                         ["operational", "contractual", "contractual", "risk"])
        all_metrics = [metric for item in recommendation.actions for metric in item.supporting_metrics]
        self.assertIn(("SHORT operativo", "0.5 GWh"), all_metrics)
        self.assertIn(("Exceso contractual", "0.2 GWh"), all_metrics)
        self.assertIn(("Déficit take-or-pay proyectado", "2.8 GWh"), all_metrics)
        self.assertIn(("Delta de exposición", "+4,200 EUR"), all_metrics)
        self.assertNotIn("3.3 GWh", str(recommendation.actions))

    def test_balanced_and_top_above_do_not_create_artificial_actions(self):
        balanced = compose_business_recommendation(SimpleNamespace(
            specialist_results=[specialist("ProcurementAgent", procurement("BALANCED", 0, None))]))
        self.assertEqual(balanced.actions, ())
        projection = calculate_take_or_pay_projection(40.8, 50, 8)
        above = compose_business_recommendation(SimpleNamespace(
            specialist_results=[specialist(
                "CommercialAgent",
                SimpleNamespace(calculations=[], contract_facts=[],
                                take_or_pay_projection=projection),
            )]
        ))
        self.assertEqual(above.actions, ())


if __name__ == "__main__":
    unittest.main()
