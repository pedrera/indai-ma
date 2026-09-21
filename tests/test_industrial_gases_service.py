from datetime import datetime, timedelta, timezone
import unittest

from industrial_gases import (
    Application, ConsumptionForecast, ConsumptionRate, DeliveryPlan, GasProduct,
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
        application=Application("app-1", "site-1", "critical oxygen"),
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
            application=Application("map", "site-1", "MAP"),
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


if __name__ == "__main__":
    unittest.main()
