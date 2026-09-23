from dataclasses import fields, replace
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import Mock, call

from industrial_gases import (
    Application,
    ApplicationGasRequirement,
    ConsumptionForecast,
    ConsumptionRate,
    Customer,
    DeliveryPlan,
    GasProduct,
    InventorySnapshot,
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


def make_request(*, kind: str, inventory: int | None = None) -> SupplyAssuranceRequest:
    if kind == "o2":
        customer = Customer("hospital-costa-sur", "Hospital Costa Sur")
        site = Site("hospital-main", customer.customer_id, "Hospital Costa Sur")
        application = Application(
            "medical-oxygen", site.site_id, "Medical oxygen",
            (ApplicationGasRequirement("o2-medical", "patient care"),),
        )
        product = GasProduct("o2-medical", "Medicinal oxygen", "liquid")
        installation = SupplyInstallation(
            "hospital-o2-tank", site.site_id, product.gas_product_id,
            "bulk", "cryogenic_tank", Quantity(10000, "kg"),
        )
        unit, default_inventory, rate, delivery, safety = "kg", 3200, 700, 4000, 1500
    elif kind == "co2":
        customer = Customer("alimentos-del-sur", "Alimentos del Sur")
        site = Site("malaga-plant", customer.customer_id, "Málaga Production Plant")
        application = Application(
            "beverage-carbonation", site.site_id, "Beverage carbonation",
            (ApplicationGasRequirement("co2", "carbonation"),),
        )
        product = GasProduct("co2", "CO2", "gas")
        installation = SupplyInstallation(
            "malaga-co2-tank", site.site_id, product.gas_product_id,
            "bulk", "storage_installation", Quantity(1000, "kg"),
        )
        unit, default_inventory, rate, delivery, safety = "kg", 300, 50, 250, 150
    elif kind == "n2":
        customer = Customer("alimentos-del-sur", "Alimentos del Sur")
        site = Site("malaga-plant", customer.customer_id, "Málaga Production Plant")
        application = Application(
            "modified-atmosphere", site.site_id, "Modified atmosphere / inerting",
            (ApplicationGasRequirement("n2", "inerting"),),
        )
        product = GasProduct("n2", "N2", "gas")
        installation = SupplyInstallation(
            "malaga-n2-tank", site.site_id, product.gas_product_id,
            "bulk", "storage_installation", Quantity(2000, "Nm3"),
        )
        unit, default_inventory, rate, delivery, safety = "Nm3", 900, 100, 300, 400
    else:
        raise AssertionError(f"unexpected fixture kind: {kind}")

    current_inventory = default_inventory if inventory is None else inventory
    return SupplyAssuranceRequest(
        customer_id=customer.customer_id,
        site=site,
        application=application,
        gas_product=product,
        installation=installation,
        inventory_snapshot=InventorySnapshot(
            installation.installation_id, REFERENCE, Quantity(current_inventory, unit),
        ),
        consumption_forecast=ConsumptionForecast(
            installation.installation_id, REFERENCE, REFERENCE + timedelta(days=10),
            ConsumptionRate(rate, unit),
        ),
        delivery_plan=DeliveryPlan(
            installation.installation_id, REFERENCE + timedelta(days=4), Quantity(delivery, unit),
        ),
        safety_stock=Quantity(safety, unit),
        reference_time=REFERENCE,
    )


def canonical_portfolio() -> SupplyPortfolioRequest:
    return SupplyPortfolioRequest((
        SupplyPortfolioItem("hospital-costa-sur-o2", make_request(kind="o2")),
        SupplyPortfolioItem("alimentos-sur-malaga-co2", make_request(kind="co2")),
        SupplyPortfolioItem("alimentos-sur-malaga-n2", make_request(kind="n2")),
    ))


class SupplyPortfolioTests(unittest.TestCase):
    def test_canonical_portfolio_preserves_order_identity_units_and_item_findings(self):
        result = SupplyPortfolioService().evaluate(canonical_portfolio())

        self.assertEqual(len(result.items), 3)
        self.assertEqual(
            [item.item_id for item in result.items],
            ["hospital-costa-sur-o2", "alimentos-sur-malaga-co2", "alimentos-sur-malaga-n2"],
        )
        o2, co2, n2 = result.items
        self.assertEqual((o2.result.status, co2.result.status, n2.result.status),
                         ("COMPLETED", "COMPLETED", "COMPLETED"))
        self.assertEqual((o2.request.customer_id, o2.request.site.site_id,
                          o2.request.application.application_id, o2.request.gas_product.gas_product_id,
                          o2.request.installation.installation_id),
                         ("hospital-costa-sur", "hospital-main", "medical-oxygen",
                          "o2-medical", "hospital-o2-tank"))
        self.assertEqual((co2.request.customer_id, co2.request.site.site_id,
                          co2.request.application.application_id, co2.request.gas_product.gas_product_id,
                          co2.request.installation.installation_id),
                         ("alimentos-del-sur", "malaga-plant", "beverage-carbonation",
                          "co2", "malaga-co2-tank"))
        self.assertEqual((n2.request.customer_id, n2.request.site.site_id,
                          n2.request.application.application_id, n2.request.gas_product.gas_product_id,
                          n2.request.installation.installation_id),
                         ("alimentos-del-sur", "malaga-plant", "modified-atmosphere",
                          "n2", "malaga-n2-tank"))
        self.assertEqual(o2.result.projection.current_inventory, Quantity(3200, "kg"))
        self.assertEqual(co2.result.projection.current_inventory, Quantity(300, "kg"))
        self.assertEqual(n2.result.projection.current_inventory, Quantity(900, "Nm3"))
        self.assertEqual(n2.result.projection.consumption_until_delivery, Quantity(400, "Nm3"))
        self.assertEqual([finding.code for finding in o2.result.findings], ["safety_stock_breach"])
        self.assertEqual([finding.code for finding in co2.result.findings], ["safety_stock_breach"])
        self.assertEqual(n2.result.findings, ())
        self.assertEqual({item.result.projection.gas_product_id for item in result.items},
                         {"o2-medical", "co2", "n2"})

    def test_invalid_and_missing_items_do_not_block_completed_items(self):
        completed_request = make_request(kind="o2")
        co2_request = make_request(kind="co2")
        invalid_request = replace(
            co2_request,
            installation=replace(co2_request.installation, gas_product_id="wrong-product"),
        )
        missing_request = replace(make_request(kind="n2"), safety_stock=None)
        request = SupplyPortfolioRequest((
            SupplyPortfolioItem("done", completed_request),
            SupplyPortfolioItem("invalid", invalid_request),
            SupplyPortfolioItem("missing", missing_request),
        ))

        result = SupplyPortfolioService().evaluate(request)

        self.assertEqual([item.item_id for item in result.items], ["done", "invalid", "missing"])
        completed, invalid, missing = result.items
        self.assertEqual(completed.result.status, "COMPLETED")
        self.assertIsNotNone(completed.result.projection)
        self.assertEqual(invalid.result.status, "INVALID")
        self.assertIsNone(invalid.result.projection)
        self.assertEqual(invalid.result.missing_inputs, ())
        self.assertIn("installation_product_mismatch", invalid.result.validation_errors)
        self.assertEqual(missing.result.status, "MISSING_INPUTS")
        self.assertIsNone(missing.result.projection)
        self.assertEqual(missing.result.missing_inputs, ("safety_stock",))
        self.assertEqual(missing.result.validation_errors, ())
        self.assertEqual(result.items[0].result.status, "COMPLETED")

    def test_changing_only_co2_leaves_o2_n2_order_and_identities_unchanged(self):
        baseline_request = canonical_portfolio()
        service = SupplyPortfolioService()
        baseline = service.evaluate(baseline_request)
        changed_co2 = replace(
            baseline_request.items[1],
            request=replace(
                baseline_request.items[1].request,
                inventory_snapshot=replace(
                    baseline_request.items[1].request.inventory_snapshot,
                    inventory=Quantity(500, "kg"),
                ),
            ),
        )
        changed_request = SupplyPortfolioRequest((baseline_request.items[0], changed_co2,
                                                  baseline_request.items[2]))

        changed = service.evaluate(changed_request)

        self.assertEqual(changed.items[1].result.projection.current_inventory, Quantity(500, "kg"))
        self.assertEqual(changed.items[0], baseline.items[0])
        self.assertEqual(changed.items[2], baseline.items[2])
        self.assertEqual([item.item_id for item in changed.items],
                         [item.item_id for item in baseline.items])

    def test_same_assurance_service_assesses_each_exact_request_once_in_order(self):
        requests = [make_request(kind=kind) for kind in ("o2", "co2", "n2")]
        real_service = SupplyAssuranceService()
        expected_results = [real_service.assess(request) for request in requests]
        assurance_service = Mock(spec=SupplyAssuranceService)
        assurance_service.assess.side_effect = expected_results
        portfolio_service = SupplyPortfolioService(assurance_service)
        portfolio_request = SupplyPortfolioRequest(tuple(
            SupplyPortfolioItem(item_id, request)
            for item_id, request in zip(("o2", "co2", "n2"), requests)
        ))

        result = portfolio_service.evaluate(portfolio_request)

        self.assertEqual(assurance_service.assess.call_count, 3)
        self.assertEqual(assurance_service.assess.call_args_list,
                         [call(request) for request in requests])
        for actual, expected, original_request in zip(result.items, expected_results, requests):
            self.assertIs(actual.result, expected)
            self.assertIs(actual.request, original_request)

    def test_duplicate_empty_and_whitespace_item_ids_are_rejected(self):
        request = make_request(kind="o2")
        with self.assertRaisesRegex(ValueError, "unique"):
            SupplyPortfolioRequest((SupplyPortfolioItem("same", request),
                                    SupplyPortfolioItem("same", request)))
        for item_id in ("", " ", "\t\n"):
            with self.subTest(item_id=repr(item_id)), self.assertRaisesRegex(ValueError, "non-empty"):
                SupplyPortfolioItem(item_id, request)

    def test_items_are_individual_results_without_aggregate_fields(self):
        result = SupplyPortfolioService().evaluate(canonical_portfolio())
        self.assertEqual(tuple(field.name for field in fields(result)), ("items",))
        self.assertEqual(tuple(field.name for field in fields(result.items[0])),
                         ("item_id", "request", "result"))
        for field_name in (
            "total_inventory", "total_consumption", "total_capacity", "total_delivery",
            "total_safety_stock", "total_required_delivery", "portfolio_days_of_supply",
            "combined_projection",
        ):
            self.assertFalse(hasattr(result, field_name))

    def test_empty_collections_are_preserved_without_inventing_results(self):
        result = SupplyPortfolioService().evaluate(SupplyPortfolioRequest(()))
        self.assertEqual(result.items, ())
