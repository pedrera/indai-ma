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
    product_name, application_name, unit, application_role, physical_form, application_id = {
        "oxygen-medical": ("O2 medicinal", "critical oxygen", "kg", "process", "liquid", "app-1"),
        "co2": ("CO2", "Beverage carbonation", "kg", "carbonation", "gas", "co2-application"),
        "n2": ("N2", "Modified atmosphere / inerting", "Nm3", "inerting", "gas", "n2-application"),
    }.get(product_id, (product_id, product_id, "kg", "process", "gas", f"{product_id}-application"))
    return ResolvedSupplyIdentity(
        customer=IdentityReference(Customer("customer-1", "Hospital Costa Sur")),
        site=IdentityReference(Site("site-1", "customer-1", "Hospital Costa Sur")),
        application=IdentityReference(Application(application_id, "site-1", application_name,
                                                  (ApplicationGasRequirement(product_id, application_role),))),
        gas_product=IdentityReference(GasProduct(product_id, product_name, physical_form)),
        installation=IdentityReference(SupplyInstallation(installation_id, "site-1", product_id,
                                                           "bulk", "cryogenic_tank", Quantity(10000, unit))),
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


def food_beverage_identity_context(*, second_co2_installation=False, include_argon=False):
    customer = Customer("alimentos-del-sur", "Alimentos del Sur")
    site = Site("malaga-production-plant", customer.customer_id, "Málaga Production Plant")

    def branch(product_id, product_name, application_id, application_name, role,
               installation_id=None, unit=None):
        product = GasProduct(product_id, product_name, "gas")
        installation = (
            IdentityReference(SupplyInstallation(
                installation_id, site.site_id, product_id, "bulk", "storage_installation",
                Quantity(1000, unit),
            )) if installation_id is not None else IdentityReference()
        )
        return ResolvedSupplyIdentity(
            customer=IdentityReference(customer),
            site=IdentityReference(site),
            application=IdentityReference(Application(
                application_id, site.site_id, application_name,
                (ApplicationGasRequirement(product_id, role),),
            )),
            gas_product=IdentityReference(product),
            installation=installation,
        )

    co2 = branch("co2", "CO2", "beverage-carbonation", "Beverage carbonation",
                 "carbonation", "co2-bulk-tank", "kg")
    n2 = branch("n2", "N2", "modified-atmosphere", "Modified atmosphere / inerting",
                "inerting", "n2-bulk-tank", "Nm3")
    entries = {
        "Alimentos del Sur": co2,
        "N2": n2,
        "N2 bulk tank": n2,
        "nitrogen installation": n2,
    }
    if second_co2_installation:
        entries["CO2 second installation"] = ResolvedSupplyIdentity(
            customer=co2.customer,
            site=co2.site,
            application=co2.application,
            gas_product=co2.gas_product,
            installation=IdentityReference(SupplyInstallation(
                "co2-secondary-tank", site.site_id, "co2", "bulk",
                "storage_installation", Quantity(1000, "kg"),
            )),
        )
    if include_argon:
        entries["argon"] = branch(
            "argon", "Argon", "argon-process", "Argon process", "inerting",
        )
    return SupplyAssuranceIdentityContext(entries), co2, n2


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
        n2 = composer.compose(facts(
            current_inventory=Quantity(500, "Nm3"),
            consumption_rate=ConsumptionRate(300, "Nm3"),
            planned_delivery_quantity=Quantity(1000, "Nm3"),
            safety_stock=Quantity(1000, "Nm3"),
        ), identity("n2", "n2-tank"))
        self.assertEqual(co2.request.gas_product.gas_product_id, "co2")
        self.assertEqual(n2.request.gas_product.gas_product_id, "n2")
        self.assertNotEqual(co2.request.installation.installation_id, n2.request.installation.installation_id)
        self.assertEqual(n2.request.installation.capacity.unit, "Nm3")
        self.assertEqual(n2.request.inventory_snapshot.inventory, Quantity(500, "Nm3"))
        self.assertEqual(co2.request.inventory_snapshot.inventory, Quantity(100, "kg"))

    def test_food_beverage_resolve_selects_installation_for_resolved_product(self):
        context, co2_identity, n2_identity = food_beverage_identity_context()

        co2 = context.resolve("Alimentos del Sur CO2")
        n2 = context.resolve("Alimentos del Sur N2")

        self.assertEqual(co2.customer.value.customer_id, "alimentos-del-sur")
        self.assertEqual(co2.site.value.site_id, "malaga-production-plant")
        self.assertEqual(co2.gas_product.value.gas_product_id, "co2")
        self.assertEqual(co2.installation.status, "resolved")
        self.assertIs(co2.installation.value, co2_identity.installation.value)
        self.assertEqual(n2.customer.value.customer_id, "alimentos-del-sur")
        self.assertEqual(n2.site.value.site_id, "malaga-production-plant")
        self.assertEqual(n2.gas_product.value.gas_product_id, "n2")
        self.assertEqual(n2.installation.status, "resolved")
        self.assertIs(n2.installation.value, n2_identity.installation.value)

    def test_food_beverage_resolve_preserves_ambiguity_for_compatible_installations(self):
        context, _, _ = food_beverage_identity_context(second_co2_installation=True)

        resolved = context.resolve("Alimentos del Sur CO2")

        self.assertEqual(resolved.installation.status, "ambiguous")
        self.assertEqual(set(resolved.installation.candidates),
                         {"co2-bulk-tank", "co2-secondary-tank"})
        self.assertIsNone(resolved.installation.value)

    def test_food_beverage_resolve_does_not_fall_back_to_another_gas_installation(self):
        context, _, _ = food_beverage_identity_context(include_argon=True)

        resolved = context.resolve("Alimentos del Sur Argon")

        self.assertEqual(resolved.gas_product.status, "resolved")
        self.assertEqual(resolved.gas_product.value.gas_product_id, "argon")
        self.assertEqual(resolved.installation.status, "absent")
        self.assertIsNone(resolved.installation.value)

    def test_resolve_labels_does_not_replace_explicit_inconsistent_installation(self):
        context, co2_identity, n2_identity = food_beverage_identity_context()

        resolved = context.resolve_labels({
            "customer_label": "Alimentos del Sur",
            "site_label": "Málaga Production Plant",
            "application_label": "Beverage carbonation",
            "gas_product_label": "CO2",
            "installation_label": "N2 bulk tank",
        })

        self.assertEqual(resolved.gas_product.value.gas_product_id, "co2")
        self.assertEqual(resolved.installation.status, "resolved")
        self.assertIs(resolved.installation.value, n2_identity.installation.value)
        self.assertIsNot(resolved.installation.value, co2_identity.installation.value)

        text_resolved = context.resolve("Alimentos del Sur CO2 nitrogen installation")
        self.assertEqual(text_resolved.gas_product.value.gas_product_id, "co2")
        self.assertEqual(text_resolved.installation.status, "resolved")
        self.assertIs(text_resolved.installation.value, n2_identity.installation.value)

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
