from datetime import datetime, timedelta, timezone
import unittest

from industrial_gases import (
    Application, ApplicationGasRequirement, ConsumptionForecast, ConsumptionRate,
    Customer, DeliveryPlan, GasProduct, InventorySnapshot, Quantity, Site,
    SupplyInstallation, UnitDimension, build_supply_projection, unit_spec,
)


REFERENCE = datetime(2026, 1, 1, tzinfo=timezone.utc)


class IndustrialGasesUnitTests(unittest.TestCase):
    def test_catalog_dimensions(self):
        self.assertEqual(unit_spec("kg").dimension, UnitDimension.MASS)
        self.assertEqual(unit_spec("t").dimension, UnitDimension.MASS)
        self.assertEqual(unit_spec("L").dimension, UnitDimension.LIQUID_VOLUME)
        self.assertEqual(unit_spec("m3").dimension, UnitDimension.LIQUID_VOLUME)
        self.assertEqual(unit_spec("Nm3").dimension, UnitDimension.NORMALIZED_GAS_VOLUME)
        self.assertEqual(unit_spec("Sm3").dimension, UnitDimension.NORMALIZED_GAS_VOLUME)
        self.assertNotEqual(unit_spec("Nm3").code, unit_spec("Sm3").code)

    def test_unknown_and_level_units_rejected(self):
        for unit in ("foobar", "litres-ish", "%"):
            with self.assertRaises(ValueError):
                Quantity(1, unit)
            with self.assertRaises(ValueError):
                ConsumptionRate(1, unit)

    def test_supported_quantity_dimensions_and_legacy_constructor(self):
        self.assertEqual(Quantity(1, "kg").dimension, UnitDimension.MASS)
        self.assertEqual(Quantity(1, "L").dimension, UnitDimension.LIQUID_VOLUME)
        self.assertEqual(Quantity(1, "Nm3").dimension, UnitDimension.NORMALIZED_GAS_VOLUME)
        self.assertEqual(ConsumptionRate(1, "kg", "day").quantity_unit, "kg")

    def test_compatible_operational_units(self):
        for unit in ("kg", "L", "Nm3"):
            installation = SupplyInstallation("i", "s", "p", "bulk", "tank", Quantity(100, unit))
            projection = build_supply_projection(
                installation,
                InventorySnapshot("i", REFERENCE, Quantity(20, unit)),
                ConsumptionForecast("i", REFERENCE, REFERENCE + timedelta(days=2), ConsumptionRate(5, unit)),
                DeliveryPlan("i", REFERENCE + timedelta(days=1), Quantity(10, unit)),
                Quantity(5, unit), REFERENCE,
            )
            self.assertEqual(projection.current_inventory.unit, unit)

    def test_same_dimension_different_units_are_not_converted(self):
        cases = (("kg", "t"), ("L", "m3"), ("Nm3", "Sm3"))
        for inventory_unit, rate_unit in cases:
            installation = SupplyInstallation("i", "s", "p", "bulk", "tank", Quantity(100, inventory_unit))
            with self.assertRaises(ValueError):
                build_supply_projection(
                    installation,
                    InventorySnapshot("i", REFERENCE, Quantity(20, inventory_unit)),
                    ConsumptionForecast("i", REFERENCE, REFERENCE + timedelta(days=2), ConsumptionRate(5, rate_unit)),
                    DeliveryPlan("i", REFERENCE + timedelta(days=1), Quantity(10, inventory_unit)),
                    Quantity(5, inventory_unit), REFERENCE,
                )

    def test_delivery_safety_and_capacity_units_must_match(self):
        installation = SupplyInstallation("i", "s", "p", "bulk", "tank", Quantity(100, "kg"))
        snapshot = InventorySnapshot("i", REFERENCE, Quantity(20, "kg"))
        forecast = ConsumptionForecast("i", REFERENCE, REFERENCE + timedelta(days=2), ConsumptionRate(5, "kg"))
        with self.assertRaises(ValueError):
            build_supply_projection(installation, snapshot, forecast,
                                    DeliveryPlan("i", REFERENCE + timedelta(days=1), Quantity(10, "L")), Quantity(5, "kg"), REFERENCE)
        with self.assertRaises(ValueError):
            build_supply_projection(installation, snapshot, forecast,
                                    DeliveryPlan("i", REFERENCE + timedelta(days=1), Quantity(10, "kg")), Quantity(5, "L"), REFERENCE)
        incompatible = SupplyInstallation("i", "s", "p", "bulk", "tank", Quantity(100, "L"))
        with self.assertRaises(ValueError):
            build_supply_projection(incompatible, snapshot, forecast,
                                    DeliveryPlan("i", REFERENCE + timedelta(days=1), Quantity(10, "kg")), Quantity(5, "kg"), REFERENCE)


if __name__ == "__main__":
    unittest.main()
