from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest

from industrial_gases import (
    Application, ApplicationGasRequirement, ConsumptionForecast, ConsumptionRate,
    Customer, DeliveryPlan, GasProduct, InventorySnapshot, Quantity, Site,
    SupplyInstallation, build_supply_projection, calculate_days_of_supply,
    calculate_required_delivery_volume, project_inventory,
)


UTC = timezone.utc
REFERENCE = datetime(2026, 1, 1, tzinfo=UTC)


def scenario(inventory=3200, rate=700, safety=1500, days=4, delivery=4000, capacity=10000):
    installation = SupplyInstallation("tank-1", "site-1", "oxygen-medical", "bulk", "cryogenic_tank", Quantity(capacity, "kg"))
    snapshot = InventorySnapshot("tank-1", REFERENCE, Quantity(inventory, "kg"))
    forecast = ConsumptionForecast("tank-1", REFERENCE, None, ConsumptionRate(rate, "kg"))
    plan = DeliveryPlan("tank-1", REFERENCE + timedelta(days=days), Quantity(delivery, "kg"))
    return installation, snapshot, forecast, plan, Quantity(safety, "kg")


class IndustrialGasesFoundationTests(unittest.TestCase):
    def test_canonical_hospital_projection(self):
        args = scenario()
        result = build_supply_projection(*args, REFERENCE)
        self.assertEqual(result.days_of_supply, Decimal("4.571428571428571428571428571"))
        self.assertEqual(result.consumption_until_delivery, Quantity(2800, "kg"))
        self.assertEqual(result.inventory_immediately_before_delivery, Quantity(400, "kg"))
        self.assertEqual((result.safety_stock_gap_before_delivery.value, result.safety_stock_gap_before_delivery.unit), (Decimal("-1100"), "kg"))
        self.assertFalse(result.stockout_before_delivery)
        self.assertIsNone(result.stockout_at)
        self.assertEqual(result.planned_delivery_quantity, Quantity(4000, "kg"))
        self.assertEqual(result.inventory_immediately_after_delivery, Quantity(4400, "kg"))
        self.assertEqual(result.required_delivery_volume, Quantity(1100, "kg"))
        self.assertFalse(result.capacity_exceeded)
        self.assertEqual(result.capacity_overflow, Quantity(0, "kg"))

    def test_zero_consumption_returns_none_days(self):
        installation, snapshot, forecast, plan, safety = scenario(rate=0)
        self.assertIsNone(calculate_days_of_supply(snapshot.inventory, forecast.rate))
        self.assertFalse(build_supply_projection(installation, snapshot, forecast, plan, safety, REFERENCE).stockout_before_delivery)

    def test_inventory_zero_and_stockout(self):
        result = build_supply_projection(*scenario(inventory=0), REFERENCE)
        self.assertEqual(result.days_of_supply, Decimal(0))
        self.assertTrue(result.stockout_before_delivery)
        self.assertEqual(result.stockout_at, REFERENCE)
        self.assertEqual(result.inventory_immediately_before_delivery, Quantity(0, "kg"))

    def test_safety_zero_delivery_now_and_zero_delivery_quantity(self):
        args = scenario(safety=0, days=0, delivery=0)
        result = build_supply_projection(*args, REFERENCE)
        self.assertEqual(result.consumption_until_delivery, Quantity(0, "kg"))
        self.assertEqual((result.safety_stock_gap_before_delivery.value, result.safety_stock_gap_before_delivery.unit), (Decimal("3200"), "kg"))
        self.assertEqual(result.inventory_immediately_after_delivery, Quantity(3200, "kg"))

    def test_delivery_in_past_is_rejected(self):
        with self.assertRaises(ValueError):
            build_supply_projection(*scenario(days=-1), REFERENCE)

    def test_reference_time_must_match_snapshot_and_forecast_must_cover_horizon(self):
        installation, snapshot, forecast, plan, safety = scenario()
        with self.assertRaises(ValueError):
            build_supply_projection(installation, snapshot, forecast, plan, safety, REFERENCE + timedelta(minutes=1))
        short_forecast = ConsumptionForecast("tank-1", REFERENCE + timedelta(days=1), REFERENCE + timedelta(days=3), forecast.rate)
        with self.assertRaises(ValueError):
            build_supply_projection(installation, snapshot, short_forecast, plan, safety, REFERENCE)

    def test_reaches_zero_exactly_at_delivery_without_stockout(self):
        args = scenario(inventory=2800, rate=700, days=4, delivery=0)
        result = build_supply_projection(*args, REFERENCE)
        self.assertFalse(result.stockout_before_delivery)
        self.assertIsNone(result.stockout_at)
        self.assertEqual(result.inventory_immediately_before_delivery, Quantity(0, "kg"))

    def test_positive_inventory_can_stock_out_before_delivery(self):
        result = build_supply_projection(*scenario(inventory=1000, rate=700), REFERENCE)
        self.assertTrue(result.stockout_before_delivery)
        self.assertEqual(result.stockout_at, REFERENCE + timedelta(seconds=float(Decimal(1000) / Decimal(700) * Decimal(86400))))
        self.assertEqual(result.inventory_immediately_before_delivery, Quantity(0, "kg"))

    def test_capacity_overflow_is_exposed_without_truncation(self):
        result = build_supply_projection(*scenario(delivery=11000), REFERENCE)
        self.assertTrue(result.capacity_exceeded)
        self.assertEqual(result.capacity_overflow, Quantity(1400, "kg"))
        self.assertEqual(result.planned_delivery_quantity, Quantity(11000, "kg"))
        self.assertEqual(result.inventory_immediately_after_delivery, Quantity(11400, "kg"))

    def test_required_delivery_and_units(self):
        self.assertEqual(calculate_required_delivery_volume(Quantity(3200, "kg"), Quantity(2800, "kg"), Quantity(1500, "kg")), Quantity(1100, "kg"))
        with self.assertRaises(ValueError):
            calculate_days_of_supply(Quantity(1, "kg"), ConsumptionRate(1, "Nm3"))
        with self.assertRaises((TypeError, ValueError)):
            Quantity(-1, "kg")
        with self.assertRaises((TypeError, ValueError)):
            Quantity(-1, "kg", allow_negative=True)
        installation, snapshot, forecast, plan, safety = scenario()
        with self.assertRaises((TypeError, ValueError)):
            build_supply_projection(installation, InventorySnapshot("tank-1", REFERENCE, Quantity(-1, "kg")), forecast, plan, safety, REFERENCE)
        with self.assertRaises((TypeError, ValueError)):
            SupplyInstallation("bad", "site-1", "o2", "bulk", "tank", Quantity(-1, "kg"))
        with self.assertRaises((TypeError, ValueError)):
            DeliveryPlan("tank-1", REFERENCE, Quantity(-1, "kg"))
        with self.assertRaises(ValueError):
            Quantity(1, "kg").add(Quantity(1, "Nm3"))

    def test_provenance_and_immutability(self):
        result = build_supply_projection(*scenario(), REFERENCE)
        self.assertEqual({item.origin for item in result.provenance}, {"operational_input", "configuration", "deterministic_calculation"})
        with self.assertRaises(AttributeError):
            result.current_inventory = Quantity(1, "kg")
        self.assertEqual(result, build_supply_projection(*scenario(), REFERENCE))

    def test_multigas_application_and_installations_are_independent(self):
        application = Application("map", "site-1", "MAP", (
            ApplicationGasRequirement("co2", "process"),
            ApplicationGasRequirement("n2", "inerting"),
        ))
        self.assertEqual([item.gas_product_id for item in application.gas_requirements], ["co2", "n2"])
        o2 = SupplyInstallation("o2-tank", "site-1", "o2", "bulk", "cryogenic_tank", Quantity(10000, "kg"))
        n2 = SupplyInstallation("n2-tank", "site-1", "n2", "bulk", "cryogenic_tank", Quantity(8000, "kg"))
        o2_snapshot = InventorySnapshot("o2-tank", REFERENCE, Quantity(3200, "kg"))
        n2_snapshot = InventorySnapshot("n2-tank", REFERENCE, Quantity(5000, "kg"))
        o2_forecast = ConsumptionForecast("o2-tank", REFERENCE, None, ConsumptionRate(700, "kg"))
        n2_forecast = ConsumptionForecast("n2-tank", REFERENCE, None, ConsumptionRate(300, "kg"))
        o2_plan = DeliveryPlan("o2-tank", REFERENCE + timedelta(days=4), Quantity(4000, "kg"))
        n2_plan = DeliveryPlan("n2-tank", REFERENCE + timedelta(days=4), Quantity(1000, "kg"))
        o2_result = build_supply_projection(o2, o2_snapshot, o2_forecast, o2_plan, Quantity(1500, "kg"), REFERENCE)
        n2_result = build_supply_projection(n2, n2_snapshot, n2_forecast, n2_plan, Quantity(1000, "kg"), REFERENCE)
        self.assertNotEqual(o2.gas_product_id, n2.gas_product_id)
        self.assertEqual((o2_result.installation_id, o2_result.gas_product_id), ("o2-tank", "o2"))
        self.assertEqual((n2_result.installation_id, n2_result.gas_product_id), ("n2-tank", "n2"))
        self.assertEqual(o2_result.current_inventory, Quantity(3200, "kg"))
        self.assertEqual(n2_result.current_inventory, Quantity(5000, "kg"))
        self.assertNotEqual(o2_result.consumption_until_delivery, n2_result.consumption_until_delivery)
        self.assertEqual(o2_result, build_supply_projection(o2, o2_snapshot, o2_forecast, o2_plan, Quantity(1500, "kg"), REFERENCE))

    def test_projection_rejects_mismatched_installation(self):
        installation, snapshot, forecast, plan, safety = scenario()
        with self.assertRaises(ValueError):
            build_supply_projection(installation, InventorySnapshot("other", REFERENCE, Quantity(1, "kg")), forecast, plan, safety, REFERENCE)


if __name__ == "__main__":
    unittest.main()
