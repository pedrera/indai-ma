from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch

from industrial_gases import (
    Application, ApplicationGasRequirement, ConsumptionForecast, ConsumptionRate, DeliveryPlan, GasProduct,
    InventorySnapshot, Quantity, Site, SupplyAssuranceRequest,
    SupplyAssuranceService, SupplyInstallation,
)


UTC = timezone.utc
REFERENCE = datetime(2026, 1, 1, tzinfo=UTC)


def request(demand_rate=700, inventory=3200, delivery_days=4):
    installation = SupplyInstallation("tank-1", "site-1", "co2", "bulk", "cryogenic_tank", Quantity(10000, "kg"))
    return SupplyAssuranceRequest(
        customer_id="customer-1",
        site=Site("site-1", "customer-1", "Hospital Costa Sur"),
        application=Application("app-1", "site-1", "critical oxygen",
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

    def test_map_application_can_declare_two_gases_without_aggregation(self):
        base = request()
        n2 = SupplyInstallation("n2-tank", "site-1", "nitrogen", "bulk", "cryogenic_tank", Quantity(8000, "kg"))
        n2_request = SupplyAssuranceRequest(
            customer_id=base.customer_id, site=base.site,
            application=Application("map", "site-1", "MAP",
                                    (ApplicationGasRequirement("nitrogen", "inerting"),)),
            gas_product=GasProduct("nitrogen", "N2", "liquid"), installation=n2,
            inventory_snapshot=InventorySnapshot("n2-tank", REFERENCE, Quantity(5000, "kg")),
            consumption_forecast=ConsumptionForecast("n2-tank", REFERENCE, REFERENCE + timedelta(days=10), ConsumptionRate(300, "kg")),
            delivery_plan=DeliveryPlan("n2-tank", REFERENCE + timedelta(days=4), Quantity(1000, "kg")),
            safety_stock=Quantity(1000, "kg"), reference_time=REFERENCE)
        co2_result = SupplyAssuranceService().assess(base)
        n2_result = SupplyAssuranceService().assess(n2_request)
        self.assertEqual((co2_result.status, n2_result.status), ("COMPLETED", "COMPLETED"))
        self.assertEqual((co2_result.gas_product_id, n2_result.gas_product_id), ("co2", "nitrogen"))
        self.assertNotEqual(co2_result.installation_id, n2_result.installation_id)
        self.assertNotEqual(co2_result.projection.current_inventory, n2_result.projection.current_inventory)
        self.assertNotEqual(co2_result.projection, n2_result.projection)
        changed_o2 = SupplyAssuranceService().assess(request(inventory=1000))
        self.assertNotEqual(changed_o2.projection.current_inventory, co2_result.projection.current_inventory)
        self.assertEqual(n2_result.projection.current_inventory, Quantity(5000, "kg"))

    def test_relationship_mismatches_are_structured_invalid(self):
        base = request()
        cases = (
            ("site_customer_mismatch", Site("site-1", "other", "Hospital Costa Sur")),
            ("application_site_mismatch", Application("app-1", "other", "critical oxygen",
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
        invalid = SupplyAssuranceRequest(**{**base.__dict__, "application": Application("app-1", "site-1", "critical oxygen")})
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
