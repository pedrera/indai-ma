from datetime import datetime, timedelta, timezone
from dataclasses import replace
import unittest
from unittest.mock import patch

from industrial_gases import (
    Application, ApplicationGasRequirement, ConsumptionForecast, ConsumptionRate, DeliveryPlan, GasProduct,
    Customer, InventorySnapshot, Quantity, Site, SupplyAssuranceRequest,
    SupplyAssuranceService, SupplyInstallation,
)


UTC = timezone.utc
REFERENCE = datetime(2026, 1, 1, tzinfo=UTC)


def request(demand_rate=700, inventory=3200, delivery_days=4):
    installation = SupplyInstallation("tank-1", "site-1", "co2", "bulk", "cryogenic_tank", Quantity(10000, "kg"))
    return SupplyAssuranceRequest(
        customer_id="customer-1",
        site=Site("site-1", "customer-1", "Hospital Costa Sur"),
        application=Application("app-1", "site-1", "CO2 process",
                                (ApplicationGasRequirement("co2", "process"),)),
        gas_product=GasProduct("co2", "CO2", "liquid"),
        installation=installation,
        inventory_snapshot=InventorySnapshot("tank-1", REFERENCE, Quantity(inventory, "kg")),
        consumption_forecast=ConsumptionForecast("tank-1", REFERENCE, REFERENCE + timedelta(days=10), ConsumptionRate(demand_rate, "kg")),
        delivery_plan=DeliveryPlan("tank-1", REFERENCE + timedelta(days=delivery_days), Quantity(4000, "kg")),
        safety_stock=Quantity(1500, "kg"),
        reference_time=REFERENCE,
    )


class SupplyAssuranceServiceTests(unittest.TestCase):
    def test_canonical_request_returns_projection_and_identity(self):
        result = SupplyAssuranceService().assess(request())
        self.assertEqual(result.status, "COMPLETED")
        self.assertEqual((result.customer_id, result.site_id, result.application_id,
                          result.gas_product_id, result.installation_id),
                         ("customer-1", "site-1", "app-1", "co2", "tank-1"))
        self.assertEqual(result.projection.inventory_immediately_before_delivery, Quantity(400, "kg"))
        self.assertTrue(any(item.code == "safety_stock_breach" for item in result.findings))

    def test_missing_inputs_are_explicit_and_not_invalid(self):
        base = request()
        result = SupplyAssuranceService().assess(SupplyAssuranceRequest(
            customer_id=base.customer_id, site=base.site, application=base.application,
            gas_product=base.gas_product, installation=base.installation,
            inventory_snapshot=base.inventory_snapshot, consumption_forecast=None,
            delivery_plan=base.delivery_plan, safety_stock=base.safety_stock,
            reference_time=base.reference_time))
        self.assertEqual(result.status, "MISSING_INPUTS")
        self.assertEqual(result.missing_inputs, ("consumption_forecast",))
        self.assertEqual(result.validation_errors, ())
        self.assertIsNone(result.projection)

    def test_invalid_request_is_distinct_from_missing_input(self):
        base = request()
        invalid = SupplyAssuranceRequest(**{**base.__dict__, "reference_time": REFERENCE + timedelta(minutes=1)})
        result = SupplyAssuranceService().assess(invalid)
        self.assertEqual(result.status, "INVALID")
        self.assertEqual(result.missing_inputs, ())
        self.assertTrue(result.validation_errors)
        self.assertIsNone(result.projection)

    def test_findings_are_structured_and_deterministic(self):
        first = SupplyAssuranceService().assess(request(inventory=0))
        second = SupplyAssuranceService().assess(request(inventory=0))
        self.assertEqual(first, second)
        self.assertEqual(first.findings[0].code, "stockout_before_delivery")
        self.assertEqual(first.findings[0].source_fields, ("stockout_before_delivery", "stockout_at"))

    def test_food_and_beverage_co2_and_n2_graphs_are_independent_at_service_boundary(self):
        customer = Customer("alimentos-del-sur", "Alimentos del Sur")
        site = Site("malaga-production-plant", customer.customer_id, "Málaga Production Plant")

        co2_product = GasProduct("co2", "CO2", "gas")
        co2_installation = SupplyInstallation(
            "co2-bulk-tank", site.site_id, co2_product.gas_product_id,
            "bulk", "cryogenic_tank", Quantity(1000, "kg"),
        )
        co2_request = SupplyAssuranceRequest(
            customer_id=customer.customer_id,
            site=site,
            application=Application(
                "beverage-carbonation", site.site_id, "Beverage carbonation",
                (ApplicationGasRequirement(co2_product.gas_product_id, "carbonation"),),
            ),
            gas_product=co2_product,
            installation=co2_installation,
            inventory_snapshot=InventorySnapshot(
                co2_installation.installation_id, REFERENCE, Quantity(300, "kg"),
            ),
            consumption_forecast=ConsumptionForecast(
                co2_installation.installation_id, REFERENCE,
                REFERENCE + timedelta(days=10), ConsumptionRate(50, "kg"),
            ),
            delivery_plan=DeliveryPlan(
                co2_installation.installation_id,
                REFERENCE + timedelta(days=4), Quantity(250, "kg"),
            ),
            safety_stock=Quantity(150, "kg"),
            reference_time=REFERENCE,
        )

        n2_product = GasProduct("n2", "N2", "gas")
        n2_installation = SupplyInstallation(
            "n2-bulk-tank", site.site_id, n2_product.gas_product_id,
            "bulk", "cryogenic_tank", Quantity(2000, "Nm3"),
        )
        n2_request = SupplyAssuranceRequest(
            customer_id=customer.customer_id,
            site=site,
            application=Application(
                "modified-atmosphere-inerting", site.site_id,
                "Modified atmosphere / inerting",
                (ApplicationGasRequirement(n2_product.gas_product_id, "inerting"),),
            ),
            gas_product=n2_product,
            installation=n2_installation,
            inventory_snapshot=InventorySnapshot(
                n2_installation.installation_id, REFERENCE, Quantity(900, "Nm3"),
            ),
            consumption_forecast=ConsumptionForecast(
                n2_installation.installation_id, REFERENCE,
                REFERENCE + timedelta(days=10), ConsumptionRate(100, "Nm3"),
            ),
            delivery_plan=DeliveryPlan(
                n2_installation.installation_id,
                REFERENCE + timedelta(days=4), Quantity(300, "Nm3"),
            ),
            safety_stock=Quantity(400, "Nm3"),
            reference_time=REFERENCE,
        )

        # Independent application requests share a site and are assessed by one service.
        service = SupplyAssuranceService()
        co2_result = service.assess(co2_request)
        n2_result = service.assess(n2_request)
        self.assertEqual((co2_result.status, n2_result.status), ("COMPLETED", "COMPLETED"))
        self.assertEqual(co2_result.customer_id, n2_result.customer_id)
        self.assertEqual(co2_result.site_id, n2_result.site_id)
        self.assertNotEqual(co2_result.application_id, n2_result.application_id)
        self.assertEqual((co2_result.gas_product_id, n2_result.gas_product_id), ("co2", "n2"))
        self.assertNotEqual(co2_result.installation_id, n2_result.installation_id)
        self.assertEqual(
            (co2_result.projection.gas_product_id, co2_result.projection.installation_id),
            ("co2", "co2-bulk-tank"),
        )
        self.assertEqual(co2_result.projection.current_inventory, Quantity(300, "kg"))
        self.assertEqual(co2_result.projection.consumption_until_delivery, Quantity(200, "kg"))
        self.assertEqual(co2_result.projection.inventory_immediately_before_delivery, Quantity(100, "kg"))
        self.assertEqual([item.code for item in co2_result.findings], ["safety_stock_breach"])
        self.assertEqual(co2_result.findings[0].source_fields,
                         ("safety_stock_gap_before_delivery",))

        self.assertEqual(
            (n2_result.projection.gas_product_id, n2_result.projection.installation_id),
            ("n2", "n2-bulk-tank"),
        )
        self.assertEqual(n2_result.projection.current_inventory, Quantity(900, "Nm3"))
        self.assertEqual(n2_result.projection.consumption_until_delivery, Quantity(400, "Nm3"))
        self.assertEqual(n2_result.projection.inventory_immediately_before_delivery, Quantity(500, "Nm3"))
        self.assertEqual(n2_result.findings, ())

        # Each result remains independent; no combined site inventory is created.
        self.assertNotEqual(co2_result.projection.current_inventory.unit,
                            n2_result.projection.current_inventory.unit)
        self.assertNotEqual(co2_result.projection, n2_result.projection)
        changed_co2 = service.assess(replace(
            co2_request,
            inventory_snapshot=replace(co2_request.inventory_snapshot,
                                       inventory=Quantity(50, "kg")),
        ))
        self.assertEqual(changed_co2.status, "COMPLETED")
        self.assertEqual(changed_co2.projection.current_inventory, Quantity(50, "kg"))
        self.assertEqual(n2_result.projection.current_inventory, Quantity(900, "Nm3"))
        self.assertEqual(n2_result.projection.inventory_immediately_before_delivery, Quantity(500, "Nm3"))

    def test_service_rejects_co2_request_with_n2_snapshot(self):
        co2_request = request()
        n2_request = SupplyAssuranceRequest(
            customer_id=co2_request.customer_id,
            site=co2_request.site,
            application=Application("n2-app", "site-1", "Modified atmosphere / inerting",
                                    (ApplicationGasRequirement("n2", "inerting"),)),
            gas_product=GasProduct("n2", "N2", "gas"),
            installation=SupplyInstallation("n2-tank", "site-1", "n2", "bulk", "tank", Quantity(1000, "Nm3")),
            inventory_snapshot=InventorySnapshot("n2-tank", REFERENCE, Quantity(500, "Nm3")),
            consumption_forecast=ConsumptionForecast("n2-tank", REFERENCE, REFERENCE + timedelta(days=10), ConsumptionRate(50, "Nm3")),
            delivery_plan=DeliveryPlan("n2-tank", REFERENCE + timedelta(days=4), Quantity(100, "Nm3")),
            safety_stock=Quantity(100, "Nm3"),
            reference_time=REFERENCE,
        )
        service = SupplyAssuranceService()
        invalid = service.assess(replace(co2_request, inventory_snapshot=n2_request.inventory_snapshot))
        self.assertEqual(invalid.status, "INVALID")
        self.assertIsNone(invalid.projection)
        self.assertEqual(invalid.missing_inputs, ())
        self.assertIn("snapshot_installation_mismatch", invalid.validation_errors)

        cross_wired_product = service.assess(replace(
            co2_request,
            gas_product=n2_request.gas_product,
            installation=n2_request.installation,
            inventory_snapshot=n2_request.inventory_snapshot,
            consumption_forecast=n2_request.consumption_forecast,
            delivery_plan=n2_request.delivery_plan,
            safety_stock=n2_request.safety_stock,
        ))
        self.assertEqual(cross_wired_product.status, "INVALID")
        self.assertIsNone(cross_wired_product.projection)
        self.assertIn("application_gas_product_not_declared", cross_wired_product.validation_errors)

    def test_relationship_mismatches_are_structured_invalid(self):
        base = request()
        cases = (
            ("site_customer_mismatch", Site("site-1", "other", "Hospital Costa Sur")),
            ("application_site_mismatch", Application("app-1", "other", "CO2 process",
                                                        (ApplicationGasRequirement("co2", "process"),))),
            ("installation_product_mismatch", SupplyInstallation("tank-1", "site-1", "n2", "bulk", "cryogenic_tank", Quantity(10000, "kg"))),
            ("snapshot_installation_mismatch", InventorySnapshot("other", REFERENCE, Quantity(3200, "kg"))),
            ("forecast_installation_mismatch", ConsumptionForecast("other", REFERENCE, REFERENCE + timedelta(days=10), ConsumptionRate(700, "kg"))),
            ("delivery_installation_mismatch", DeliveryPlan("other", REFERENCE + timedelta(days=4), Quantity(4000, "kg"))),
        )
        for code, value in cases:
            field = code.split("_mismatch")[0]
            kwargs = {**base.__dict__}
            if field == "site_customer": kwargs["site"] = value
            elif field == "application_site": kwargs["application"] = value
            elif field == "installation_product": kwargs["installation"] = value
            elif field == "snapshot_installation": kwargs["inventory_snapshot"] = value
            elif field == "forecast_installation": kwargs["consumption_forecast"] = value
            elif field == "delivery_installation": kwargs["delivery_plan"] = value
            result = SupplyAssuranceService().assess(SupplyAssuranceRequest(**kwargs))
            self.assertEqual(result.status, "INVALID")
            self.assertIn(code, result.validation_errors)
            self.assertEqual(result.missing_inputs, ())
            self.assertIsNone(result.projection)

    def test_undeclared_application_gas_is_invalid(self):
        base = request()
        invalid = SupplyAssuranceRequest(**{**base.__dict__, "application": Application("app-1", "site-1", "CO2 process")})
        result = SupplyAssuranceService().assess(invalid)
        self.assertEqual(result.status, "INVALID")
        self.assertIn("application_gas_product_not_declared", result.validation_errors)

    def test_incompatible_units_are_invalid_at_service_boundary(self):
        base = request()
        invalid = SupplyAssuranceRequest(**{**base.__dict__, "safety_stock": Quantity(1500, "Nm3")})
        result = SupplyAssuranceService().assess(invalid)
        self.assertEqual(result.status, "INVALID")
        self.assertTrue(result.validation_errors)
        self.assertIsNone(result.projection)

    def test_unexpected_type_error_propagates(self):
        with patch("industrial_gases.service.build_supply_projection", side_effect=TypeError("programming bug")):
            with self.assertRaises(TypeError):
                SupplyAssuranceService().assess(request())


if __name__ == "__main__":
    unittest.main()
