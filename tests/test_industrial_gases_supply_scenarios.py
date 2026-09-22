from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest
from unittest.mock import Mock

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
    SupplyAssuranceAlternative,
    SupplyAssuranceRequest,
    SupplyAssuranceService,
    SupplyInstallation,
    evaluate_supply_assurance_alternative,
)


REFERENCE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def canonical_request():
    customer = Customer("hospital-costa-sur", "Hospital Costa Sur")
    site = Site("hospital-site", customer.customer_id, "Hospital Costa Sur")
    product = GasProduct("medical-oxygen", "O2 medicinal", "liquid")
    installation = SupplyInstallation(
        "hospital-o2-tank", site.site_id, product.gas_product_id,
        "bulk", "cryogenic_tank", Quantity(10000, "kg"),
    )
    delivery_at = REFERENCE_TIME + timedelta(days=4)
    return SupplyAssuranceRequest(
        customer_id=customer.customer_id,
        site=site,
        application=Application(
            "hospital-medical-oxygen", site.site_id, "Medical oxygen",
            (ApplicationGasRequirement(product.gas_product_id, "medical_supply"),),
        ),
        gas_product=product,
        installation=installation,
        inventory_snapshot=InventorySnapshot(
            installation.installation_id, REFERENCE_TIME, Quantity(3200, "kg"),
        ),
        consumption_forecast=ConsumptionForecast(
            installation.installation_id, REFERENCE_TIME, delivery_at,
            ConsumptionRate(700, "kg", "day"),
        ),
        delivery_plan=DeliveryPlan(
            installation.installation_id, delivery_at, Quantity(4000, "kg"),
        ),
        safety_stock=Quantity(1500, "kg"),
        reference_time=REFERENCE_TIME,
    )


def alternative(request, alternative_id, label, **changes):
    return SupplyAssuranceAlternative(
        id=alternative_id,
        label=label,
        alternative_request=replace(request, **changes),
    )


class IndustrialGasesSupplyScenarioTests(unittest.TestCase):
    def setUp(self):
        self.service = SupplyAssuranceService()
        self.baseline_request = canonical_request()

    def evaluate(self, scenario):
        return evaluate_supply_assurance_alternative(
            self.baseline_request, scenario, self.service,
        )

    def test_baseline_is_preserved_and_service_assesses_both_requests(self):
        baseline_before = self.baseline_request
        alt = alternative(
            self.baseline_request,
            "earlier-delivery",
            "Earlier delivery",
            delivery_plan=replace(
                self.baseline_request.delivery_plan,
                planned_delivery_at=REFERENCE_TIME + timedelta(days=3),
            ),
        )
        service = Mock(wraps=self.service)

        result = evaluate_supply_assurance_alternative(self.baseline_request, alt, service)

        self.assertEqual(service.assess.call_count, 2)
        self.assertIs(service.assess.call_args_list[0].args[0], self.baseline_request)
        self.assertIs(service.assess.call_args_list[1].args[0], alt.alternative_request)
        self.assertEqual(result.alternative, alt)
        self.assertEqual(result.baseline_result.status, "COMPLETED")
        self.assertEqual(result.alternative_result.status, "COMPLETED")
        self.assertEqual(self.baseline_request, baseline_before)

    def test_earlier_delivery_recalculates_only_time_dependent_projection_values(self):
        base_request = self.baseline_request
        alt = alternative(
            base_request,
            "earlier-delivery",
            "Earlier delivery",
            delivery_plan=replace(
                base_request.delivery_plan,
                planned_delivery_at=REFERENCE_TIME + timedelta(days=3),
            ),
        )

        result = self.evaluate(alt)
        base = result.baseline_result.projection
        changed = result.alternative_result.projection

        self.assertEqual(result.alternative_result.status, "COMPLETED")
        self.assertEqual(changed.consumption_until_delivery, Quantity(2100, "kg"))
        self.assertEqual(changed.inventory_immediately_before_delivery, Quantity(1100, "kg"))
        self.assertEqual(changed.safety_stock_gap_before_delivery.value, -400)
        self.assertEqual(changed.inventory_immediately_after_delivery, Quantity(5100, "kg"))
        self.assertEqual(changed.required_delivery_volume, Quantity(400, "kg"))
        self.assertFalse(changed.stockout_before_delivery)
        self.assertEqual(changed.days_of_supply, base.days_of_supply)
        self.assertEqual(changed.planned_delivery_quantity, base.planned_delivery_quantity)
        self.assertEqual(changed.current_inventory, base.current_inventory)
        self.assertEqual(changed.safety_stock, base.safety_stock)
        self.assertEqual((changed.installation_id, changed.gas_product_id),
                         (base.installation_id, base.gas_product_id))
        self.assertEqual(base.consumption_until_delivery, Quantity(2800, "kg"))
        self.assertEqual(base.inventory_immediately_before_delivery, Quantity(400, "kg"))

    def test_changed_delivery_quantity_affects_post_delivery_and_capacity_only(self):
        base_request = self.baseline_request
        alt = alternative(
            base_request,
            "larger-explicit-delivery",
            "Explicit delivery quantity scenario",
            delivery_plan=replace(
                base_request.delivery_plan,
                planned_quantity=Quantity(10000, "kg"),
            ),
        )

        result = self.evaluate(alt)
        base = result.baseline_result.projection
        changed = result.alternative_result.projection

        self.assertEqual(result.alternative_result.status, "COMPLETED")
        self.assertEqual(changed.planned_delivery_quantity, Quantity(10000, "kg"))
        self.assertEqual(changed.inventory_immediately_after_delivery, Quantity(10400, "kg"))
        self.assertTrue(changed.capacity_exceeded)
        self.assertEqual(changed.capacity_overflow, Quantity(400, "kg"))
        self.assertEqual(changed.consumption_until_delivery, base.consumption_until_delivery)
        self.assertEqual(changed.inventory_immediately_before_delivery,
                         base.inventory_immediately_before_delivery)
        self.assertEqual(changed.safety_stock_gap_before_delivery,
                         base.safety_stock_gap_before_delivery)
        self.assertEqual(changed.stockout_before_delivery, base.stockout_before_delivery)
        self.assertEqual(changed.required_delivery_volume, base.required_delivery_volume)
        self.assertEqual(changed.days_of_supply, base.days_of_supply)
        self.assertEqual(result.alternative.alternative_request.delivery_plan.planned_delivery_at,
                         base_request.delivery_plan.planned_delivery_at)

    def test_revised_consumption_rate_is_explicit_and_uses_same_projection_engine(self):
        base_request = self.baseline_request
        alt = alternative(
            base_request,
            "revised-consumption-forecast",
            "Explicit consumption forecast scenario",
            consumption_forecast=replace(
                base_request.consumption_forecast,
                rate=ConsumptionRate(500, "kg", "day"),
            ),
        )

        result = self.evaluate(alt)
        base = result.baseline_result.projection
        changed = result.alternative_result.projection
        changed_request = result.alternative.alternative_request

        self.assertEqual(changed_request.consumption_forecast.rate, ConsumptionRate(500, "kg", "day"))
        self.assertEqual(changed.days_of_supply, Decimal("6.4"))
        self.assertEqual(changed.consumption_until_delivery, Quantity(2000, "kg"))
        self.assertEqual(changed.inventory_immediately_before_delivery, Quantity(1200, "kg"))
        self.assertEqual(changed.safety_stock_gap_before_delivery.value, -300)
        self.assertEqual(changed.inventory_immediately_after_delivery, Quantity(5200, "kg"))
        self.assertEqual(changed.required_delivery_volume, Quantity(300, "kg"))
        self.assertEqual(changed.planned_delivery_quantity, base.planned_delivery_quantity)
        self.assertEqual(changed_request.delivery_plan.planned_delivery_at,
                         base_request.delivery_plan.planned_delivery_at)
        self.assertEqual((changed.installation_id, changed.gas_product_id),
                         (base.installation_id, base.gas_product_id))

    def test_missing_and_invalid_service_results_are_preserved_without_projection(self):
        base_request = self.baseline_request
        missing_request = replace(base_request, delivery_plan=None)
        missing_scenario = SupplyAssuranceAlternative(
            "missing-delivery", "Request with delivery omitted", missing_request,
        )
        missing_result = self.evaluate(missing_scenario)
        self.assertEqual(missing_result.baseline_result.status, "COMPLETED")
        self.assertEqual(missing_result.alternative_result.status, "MISSING_INPUTS")
        self.assertEqual(missing_result.alternative_result.missing_inputs, ("delivery_plan",))
        self.assertEqual(missing_result.alternative_result.validation_errors, ())
        self.assertIsNone(missing_result.alternative_result.projection)
        missing_baseline_result = evaluate_supply_assurance_alternative(
            missing_request,
            SupplyAssuranceAlternative("complete-request", "Complete request", base_request),
            self.service,
        )
        self.assertEqual(missing_baseline_result.baseline_result.status, "MISSING_INPUTS")
        self.assertEqual(missing_baseline_result.baseline_result.missing_inputs, ("delivery_plan",))
        self.assertEqual(missing_baseline_result.alternative_result.status, "COMPLETED")
        self.assertIsNone(missing_baseline_result.baseline_result.projection)

        incompatible_forecast = replace(
            base_request.consumption_forecast,
            rate=ConsumptionRate(700, "Nm3", "day"),
        )
        invalid_scenario = alternative(
            base_request,
            "incompatible-unit",
            "Invalid unit scenario",
            consumption_forecast=incompatible_forecast,
        )
        invalid_result = self.evaluate(invalid_scenario)
        self.assertEqual(invalid_result.baseline_result.status, "COMPLETED")
        self.assertEqual(invalid_result.alternative_result.status, "INVALID")
        self.assertEqual(invalid_result.alternative_result.missing_inputs, ())
        self.assertTrue(invalid_result.alternative_result.validation_errors)
        self.assertIsNone(invalid_result.alternative_result.projection)
        invalid_baseline_result = evaluate_supply_assurance_alternative(
            replace(base_request, consumption_forecast=incompatible_forecast),
            SupplyAssuranceAlternative("valid-request", "Valid request", base_request),
            self.service,
        )
        self.assertEqual(invalid_baseline_result.baseline_result.status, "INVALID")
        self.assertEqual(invalid_baseline_result.baseline_result.missing_inputs, ())
        self.assertTrue(invalid_baseline_result.baseline_result.validation_errors)
        self.assertIsNone(invalid_baseline_result.baseline_result.projection)
        self.assertEqual(invalid_baseline_result.alternative_result.status, "COMPLETED")


if __name__ == "__main__":
    unittest.main()
