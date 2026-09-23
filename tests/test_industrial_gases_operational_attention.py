from dataclasses import fields, replace
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch

from industrial_gases import (
    Application,
    ApplicationGasRequirement,
    ConsumptionForecast,
    ConsumptionRate,
    Customer,
    DeliveryPlan,
    GasProduct,
    InventorySnapshot,
    OperationalAttentionFact,
    OperationalAttentionService,
    Quantity,
    Site,
    SupplyAssuranceRequest,
    SupplyAssuranceService,
    SupplyInstallation,
    SupplyPortfolioItem,
    SupplyPortfolioRequest,
    SupplyPortfolioService,
)


REFERENCE = datetime(2026, 5, 1, tzinfo=timezone.utc)


def make_request(kind: str) -> SupplyAssuranceRequest:
    if kind == "o2":
        customer = Customer("hospital-costa-sur", "Hospital Costa Sur")
        site_id = "hospital-main"
        app_id, product_id, product_name, installation_id = (
            "medical-oxygen", "o2-medical", "Medicinal oxygen", "hospital-o2-tank"
        )
        unit, capacity, inventory, rate, safety, delivery = "kg", 10000, 3200, 700, 1500, 4000
    elif kind == "co2":
        customer = Customer("alimentos-del-sur", "Alimentos del Sur")
        site_id = "malaga-plant"
        app_id, product_id, product_name, installation_id = (
            "beverage-carbonation", "co2", "CO2", "malaga-co2-tank"
        )
        unit, capacity, inventory, rate, safety, delivery = "kg", 1000, 300, 50, 150, 250
    elif kind == "n2":
        customer = Customer("alimentos-del-sur", "Alimentos del Sur")
        site_id = "malaga-plant"
        app_id, product_id, product_name, installation_id = (
            "modified-atmosphere", "n2", "N2", "malaga-n2-tank"
        )
        unit, capacity, inventory, rate, safety, delivery = "Nm3", 2000, 900, 100, 400, 300
    else:
        raise AssertionError(f"unexpected kind: {kind}")

    site = Site(site_id, customer.customer_id, site_id)
    application = Application(
        app_id, site.site_id, app_id,
        (ApplicationGasRequirement(product_id, app_id),),
    )
    product = GasProduct(product_id, product_name, "gas")
    installation = SupplyInstallation(
        installation_id, site.site_id, product.gas_product_id,
        "bulk", "storage_installation", Quantity(capacity, unit),
    )
    delivery_at = REFERENCE + timedelta(days=4)
    return SupplyAssuranceRequest(
        customer_id=customer.customer_id,
        site=site,
        application=application,
        gas_product=product,
        installation=installation,
        inventory_snapshot=InventorySnapshot(
            installation.installation_id, REFERENCE, Quantity(inventory, unit),
        ),
        consumption_forecast=ConsumptionForecast(
            installation.installation_id, REFERENCE, REFERENCE + timedelta(days=10),
            ConsumptionRate(rate, unit),
        ),
        delivery_plan=DeliveryPlan(
            installation.installation_id, delivery_at, Quantity(delivery, unit),
        ),
        safety_stock=Quantity(safety, unit),
        reference_time=REFERENCE,
    )


def make_portfolio_request():
    return SupplyPortfolioRequest((
        SupplyPortfolioItem("hospital-costa-sur-o2", make_request("o2")),
        SupplyPortfolioItem("alimentos-sur-malaga-co2", make_request("co2")),
        SupplyPortfolioItem("alimentos-sur-malaga-n2", make_request("n2")),
    ))


class OperationalAttentionTests(unittest.TestCase):
    def test_canonical_o2_co2_n2_attention_preserves_order_and_identity(self):
        portfolio = SupplyPortfolioService().evaluate(make_portfolio_request())

        attention = OperationalAttentionService().project(portfolio)

        self.assertEqual(
            [item.item_id for item in attention.items],
            ["hospital-costa-sur-o2", "alimentos-sur-malaga-co2", "alimentos-sur-malaga-n2"],
        )
        self.assertEqual(
            [[fact.source_finding.code for fact in item.facts] for item in attention.items],
            [["safety_stock_breach"], ["safety_stock_breach"], []],
        )
        self.assertEqual(
            [(item.result.customer_id, item.result.site_id, item.result.application_id,
              item.result.gas_product_id, item.result.installation_id) for item in attention.items],
            [
                ("hospital-costa-sur", "hospital-main", "medical-oxygen", "o2-medical", "hospital-o2-tank"),
                ("alimentos-del-sur", "malaga-plant", "beverage-carbonation", "co2", "malaga-co2-tank"),
                ("alimentos-del-sur", "malaga-plant", "modified-atmosphere", "n2", "malaga-n2-tank"),
            ],
        )
        self.assertEqual(attention.items[2].facts, ())

    def test_facts_retain_exact_source_finding_without_projection_values(self):
        portfolio = SupplyPortfolioService().evaluate(make_portfolio_request())
        original = portfolio.items[0].result.findings[0]

        fact = OperationalAttentionService().project(portfolio).items[0].facts[0]

        self.assertIs(fact.source_finding, original)
        self.assertEqual(fact.source_finding.code, "safety_stock_breach")
        self.assertEqual(fact.source_finding.source_fields,
                         ("safety_stock_gap_before_delivery",))
        self.assertEqual(tuple(field.name for field in fields(fact)), ("source_finding",))

    def test_original_portfolio_result_request_and_result_are_preserved(self):
        portfolio = SupplyPortfolioService().evaluate(make_portfolio_request())

        attention = OperationalAttentionService().project(portfolio)

        for source, projected in zip(portfolio.items, attention.items):
            self.assertEqual(projected.item_id, source.item_id)
            self.assertIs(projected.request, source.request)
            self.assertIs(projected.result, source.result)
            self.assertIs(projected.result.findings, source.result.findings)

    def test_invalid_result_is_an_evaluation_problem_not_a_supply_finding(self):
        base = make_request("co2")
        invalid_request = replace(
            base,
            installation=replace(base.installation, gas_product_id="wrong-product"),
        )
        portfolio = SupplyPortfolioService().evaluate(SupplyPortfolioRequest((
            SupplyPortfolioItem("invalid", invalid_request),
        )))

        item = OperationalAttentionService().project(portfolio).items[0]

        self.assertEqual(item.evaluation_status, "INVALID")
        self.assertEqual(item.facts, ())
        self.assertEqual(item.missing_inputs, ())
        self.assertIn("installation_product_mismatch", item.validation_errors)
        self.assertIs(item.result, portfolio.items[0].result)

    def test_missing_inputs_are_information_gaps_not_supply_findings(self):
        incomplete = replace(make_request("n2"), safety_stock=None)
        portfolio = SupplyPortfolioService().evaluate(SupplyPortfolioRequest((
            SupplyPortfolioItem("missing", incomplete),
        )))

        item = OperationalAttentionService().project(portfolio).items[0]

        self.assertEqual(item.evaluation_status, "MISSING_INPUTS")
        self.assertEqual(item.facts, ())
        self.assertEqual(item.missing_inputs, ("safety_stock",))
        self.assertEqual(item.validation_errors, ())
        self.assertIs(item.result, portfolio.items[0].result)

    def test_all_current_findings_are_propagated_in_domain_order(self):
        base = make_request("o2")
        stressed = replace(
            base,
            inventory_snapshot=replace(
                base.inventory_snapshot, inventory=Quantity(1000, "kg"),
            ),
            delivery_plan=replace(base.delivery_plan, planned_quantity=Quantity(20000, "kg")),
        )
        portfolio = SupplyPortfolioService().evaluate(SupplyPortfolioRequest((
            SupplyPortfolioItem("multiple", stressed),
        )))

        item = OperationalAttentionService().project(portfolio).items[0]

        expected = ("stockout_before_delivery", "safety_stock_breach", "capacity_overflow")
        self.assertEqual(tuple(fact.source_finding.code for fact in item.facts), expected)
        self.assertEqual(tuple(f.code for f in item.result.findings), expected)
        self.assertTrue(all(fact.source_finding is source for fact, source in
                            zip(item.facts, item.result.findings)))

    def test_projects_without_reexecuting_supply_assurance(self):
        portfolio = SupplyPortfolioService().evaluate(make_portfolio_request())

        with patch.object(SupplyAssuranceService, "assess", side_effect=AssertionError("must not execute")):
            result = OperationalAttentionService().project(portfolio)

        self.assertEqual(len(result.items), 3)

    def test_attention_contract_has_no_portfolio_aggregation_fields(self):
        result = OperationalAttentionService().project(
            SupplyPortfolioService().evaluate(make_portfolio_request())
        )

        self.assertEqual(tuple(field.name for field in fields(result)), ("items",))
        self.assertEqual(tuple(field.name for field in fields(result.items[0])),
                         ("item_id", "request", "result", "facts"))
        for forbidden in (
            "total_inventory", "total_capacity", "total_findings", "portfolio_status",
            "severity", "score", "priority", "ranking", "winner", "recommendation",
        ):
            self.assertFalse(hasattr(result, forbidden))


if __name__ == "__main__":
    unittest.main()
