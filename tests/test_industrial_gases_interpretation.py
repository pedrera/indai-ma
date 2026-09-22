from datetime import datetime, timedelta, timezone
import unittest

from industrial_gases import (
    Application, ApplicationGasRequirement, ConsumptionRate, Customer,
    ExtractedSupplyFacts, ExtractionProvenance, GasProduct, IdentityReference,
    InventorySnapshot, Quantity, ResolvedSupplyIdentity, Site, SupplyAssuranceRequestComposer,
    SupplyAssuranceIdentityContext, SupplyAssuranceInterpreter, SupplyAssuranceService,
    SupplyInstallation,
)


UTC = timezone.utc
REFERENCE = datetime(2026, 1, 1, tzinfo=UTC)


def identity(product_id="oxygen-medical", installation_id="tank-1"):
    return ResolvedSupplyIdentity(
        customer=IdentityReference(Customer("customer-1", "Hospital Costa Sur")),
        site=IdentityReference(Site("site-1", "customer-1", "Hospital Costa Sur")),
        application=IdentityReference(Application("app-1", "site-1", "critical oxygen",
                                                  (ApplicationGasRequirement(product_id, "process"),))),
        gas_product=IdentityReference(GasProduct(product_id, "O2 medicinal", "liquid")),
        installation=IdentityReference(SupplyInstallation(installation_id, "site-1", product_id,
                                                           "bulk", "cryogenic_tank", Quantity(10000, "kg"))),
    )


def facts(**overrides):
    values = dict(
        current_inventory=Quantity(3200, "kg"),
        consumption_rate=ConsumptionRate(700, "kg"),
        planned_delivery_at=REFERENCE + timedelta(days=4),
        planned_delivery_quantity=Quantity(4000, "kg"),
        safety_stock=Quantity(1500, "kg"),
        reference_time=REFERENCE,
        provenance=(ExtractionProvenance("current_inventory", "explicit_input", source_text="3200 kg"),),
    )
    values.update(overrides)
    return ExtractedSupplyFacts(**values)


class IndustrialGasesInterpretationTests(unittest.TestCase):
    def test_phase_1c2_superscript_unit_aliases_remain_supported(self):
        interpreter = SupplyAssuranceInterpreter(
            SupplyAssuranceIdentityContext({"Hospital Costa Sur": identity()})
        )
        superscript = chr(0x00b3)
        aliases = (
            (f"m3{superscript}", "m3"), (f"Nm3{superscript}", "Nm3"),
            (f"Sm3{superscript}", "Sm3"), (f"nm3{superscript}", "Nm3"),
            (f"sm3{superscript}", "Sm3"),
        )
        for source_unit, expected_unit in aliases:
            with self.subTest(source_unit=source_unit):
                result = interpreter.interpret(
                    f"Stock actual: 3200 {source_unit}.", REFERENCE
                )
                self.assertEqual(result.facts.current_inventory.unit, expected_unit)

    def test_complete_healthcare_facts_compose_and_service_completes(self):
        composed = SupplyAssuranceRequestComposer().compose(facts(), identity())
        self.assertIsNotNone(composed.request)
        result = SupplyAssuranceService().assess(composed.request)
        self.assertEqual(result.status, "COMPLETED")
        self.assertEqual(result.customer_id, "customer-1")
        self.assertEqual(result.projection.current_inventory, Quantity(3200, "kg"))

    def test_facts_and_extraction_provenance_are_preserved(self):
        source = facts()
        composed = SupplyAssuranceRequestComposer().compose(source, identity())
        self.assertEqual(source.current_inventory, Quantity(3200, "kg"))
        self.assertEqual(source.provenance[0].origin, "explicit_input")
        self.assertNotIn("projection", source.__dataclass_fields__)
        self.assertEqual(composed.request.inventory_snapshot.inventory, source.current_inventory)

    def test_missing_delivery_and_safety_stock_reach_service(self):
        source = facts(planned_delivery_at=None, planned_delivery_quantity=None, safety_stock=None)
        composed = SupplyAssuranceRequestComposer().compose(source, identity())
        self.assertIsNone(composed.request.delivery_plan)
        self.assertIsNone(composed.request.safety_stock)
        result = SupplyAssuranceService().assess(composed.request)
        self.assertEqual(result.status, "MISSING_INPUTS")
        self.assertEqual(set(result.missing_inputs), {"delivery_plan", "safety_stock"})

    def test_identity_absent_unresolved_and_ambiguous_are_distinct(self):
        composer = SupplyAssuranceRequestComposer()
        absent = composer.compose(facts(), ResolvedSupplyIdentity())
        self.assertEqual(absent.missing_identities, ("customer", "site", "application", "gas_product", "installation"))
        unresolved = composer.compose(facts(), ResolvedSupplyIdentity(gas_product=IdentityReference(label="oxígeno")))
        self.assertEqual(unresolved.unresolved_identities, ("gas_product",))
        ambiguous = composer.compose(facts(), ResolvedSupplyIdentity(gas_product=IdentityReference(label="gas", candidates=("co2", "n2"))))
        self.assertEqual(ambiguous.ambiguous_identities, ("gas_product",))
        self.assertIsNone(unresolved.request)

    def test_identity_reference_invariants_and_label_normalization(self):
        resolved = IdentityReference(Customer("c1", "Corporate Name"), label="human label")
        self.assertEqual(resolved.status, "resolved")
        self.assertIsNone(IdentityReference(label="   ").label)
        self.assertEqual(IdentityReference(label="gas", candidates=(" co2 ", "n2")).candidates,
                         ("co2", "n2"))
        with self.assertRaises(ValueError):
            IdentityReference(Customer("c1", "Customer"), candidates=("c1", "c2"))
        with self.assertRaises(ValueError):
            IdentityReference(label="gas", candidates=("", "n2"))

    def test_no_ids_are_invented_and_no_implicit_clock_is_used(self):
        composed = SupplyAssuranceRequestComposer().compose(
            facts(reference_time=None, planned_delivery_at=None, planned_delivery_quantity=None), identity())
        self.assertIsNone(composed.request.reference_time)
        self.assertEqual(composed.request.customer_id, "customer-1")

    def test_co2_and_n2_compose_independent_requests(self):
        composer = SupplyAssuranceRequestComposer()
        co2 = composer.compose(facts(current_inventory=Quantity(100, "kg")), identity("co2", "co2-tank"))
        n2 = composer.compose(facts(current_inventory=Quantity(500, "kg")), identity("n2", "n2-tank"))
        self.assertEqual(co2.request.gas_product.gas_product_id, "co2")
        self.assertEqual(n2.request.gas_product.gas_product_id, "n2")
        self.assertNotEqual(co2.request.installation.installation_id, n2.request.installation.installation_id)
        self.assertEqual(n2.request.inventory_snapshot.inventory, Quantity(500, "kg"))
        self.assertEqual(co2.request.inventory_snapshot.inventory, Quantity(100, "kg"))

    def test_missing_facts_and_service_missing_inputs_are_separate(self):
        source = facts(planned_delivery_at=None, planned_delivery_quantity=None, safety_stock=None)
        composed = SupplyAssuranceRequestComposer().compose(source, identity())
        self.assertEqual(set(composed.missing_facts), {
            "planned_delivery_at", "planned_delivery_quantity", "safety_stock"})
        result = SupplyAssuranceService().assess(composed.request)
        self.assertEqual(result.status, "MISSING_INPUTS")
        self.assertEqual(set(result.missing_inputs), {"delivery_plan", "safety_stock"})
        self.assertNotEqual(composed.missing_facts, result.missing_inputs)

    def test_extraction_provenance_survives_composition(self):
        source = facts(provenance=(
            ExtractionProvenance("current_inventory", "explicit_input", "deterministic", "3200 kg"),
            ExtractionProvenance("safety_stock", "resolved_configuration", "deterministic", None),
        ))
        composed = SupplyAssuranceRequestComposer().compose(source, identity())
        self.assertEqual(composed.extraction_provenance, source.provenance)
        self.assertEqual(composed.extraction_provenance[0].source_text, "3200 kg")
        self.assertEqual(composed.extraction_provenance[1].origin, "resolved_configuration")


if __name__ == "__main__":
    unittest.main()
