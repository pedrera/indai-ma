from datetime import datetime, timedelta, timezone
import unittest

from industrial_gases import (
    Application, ApplicationGasRequirement, Customer, GasProduct, IdentityReference,
    ResolvedSupplyIdentity, Site, SupplyInstallation, Quantity, SupplyAssuranceIdentityContext,
    SupplyAssuranceInterpreter, SupplyAssuranceRequestComposer, SupplyAssuranceService,
)

REFERENCE = datetime(2026, 9, 22, 10, tzinfo=timezone(timedelta(hours=2)))


def context():
    return SupplyAssuranceIdentityContext({
        "Hospital Costa Sur": ResolvedSupplyIdentity(
            customer=IdentityReference(Customer("customer-1", "Hospital Costa Sur")),
            site=IdentityReference(Site("site-1", "customer-1", "Hospital Costa Sur")),
            application=IdentityReference(Application("app-1", "site-1", "critical oxygen",
                (ApplicationGasRequirement("oxygen", "medical"),))),
            gas_product=IdentityReference(GasProduct("oxygen", "oxígeno medicinal", "liquid")),
            installation=IdentityReference(SupplyInstallation("tank-1", "site-1", "oxygen", "bulk", "cryogenic_tank", Quantity(10000, "kg"))),
        )
    })


class InterpreterTests(unittest.TestCase):
    def setUp(self):
        self.interpreter = SupplyAssuranceInterpreter(context())

    def test_canonical_healthcare_and_service(self):
        result = self.interpreter.interpret(
            "En Hospital Costa Sur tenemos 3.200 kg de oxígeno medicinal líquido. "
            "El consumo previsto es de 700 kg al día y tenemos una entrega de 4.000 kg dentro de 4 días. "
            "El stock de seguridad es de 1.500 kg.", REFERENCE)
        self.assertEqual(result.facts.current_inventory, Quantity(3200, "kg"))
        self.assertEqual(result.facts.consumption_rate.quantity_unit, "kg")
        self.assertEqual(result.facts.planned_delivery_at, REFERENCE + timedelta(days=4))
        request = SupplyAssuranceRequestComposer().compose(result.facts, result.identity).request
        self.assertEqual(SupplyAssuranceService().assess(request).status, "COMPLETED")

    def test_equivalent_wording_and_number_formats(self):
        texts = (
            "Hospital Costa Sur. Stock actual de oxígeno: 3200 kg. Consumo diario previsto: 700 kg. Entrega prevista: 4000 kg dentro de 4 días. Stock de seguridad: 1500 kg.",
            "Hospital Costa Sur. Inventario de O2: 3 200 kg. Consumo diario: 700 kg. Entrega de 4 000 kg dentro de 4 días. Safety stock: 1 500 kg.",
        )
        results = [self.interpreter.interpret(text, REFERENCE).facts for text in texts]
        self.assertEqual(results[0].current_inventory, results[1].current_inventory)
        self.assertEqual(results[0].planned_delivery_quantity, results[1].planned_delivery_quantity)

    def test_units_preserved(self):
        for unit in ("kg", "t", "L", "m3", "Nm3", "Sm3"):
            result = self.interpreter.interpret(f"Stock actual: 3200 {unit}. Consumo diario: 700 {unit}.", REFERENCE)
            self.assertEqual(result.facts.current_inventory.unit, unit)
            self.assertEqual(result.facts.consumption_rate.quantity_unit, unit)

    def test_typographic_units_are_normalized_but_source_is_original(self):
        for source, normalized in ((f"m{chr(0x00b3)}", "m3"), (f"Nm{chr(0x00b3)}", "Nm3"), (f"Sm{chr(0x00b3)}", "Sm3")):
            result = self.interpreter.interpret(f"Stock actual: 3200 {source}. Consumo diario: 700 {source}.", REFERENCE)
            self.assertEqual(result.facts.current_inventory.unit, normalized)
            self.assertEqual(result.facts.consumption_rate.quantity_unit, normalized)
            self.assertTrue(any(source in item.source_text for item in result.facts.provenance))

    def test_missing_facts_and_unsupported_inputs(self):
        result = self.interpreter.interpret("En Hospital Costa Sur tenemos 3200 kg de oxígeno y consumimos 700 kg al día.", REFERENCE)
        self.assertIsNone(result.facts.planned_delivery_at)
        self.assertIsNone(result.facts.safety_stock)
        composed = SupplyAssuranceRequestComposer().compose(result.facts, result.identity)
        service_result = SupplyAssuranceService().assess(composed.request)
        self.assertEqual(service_result.status, "MISSING_INPUTS")
        unsupported = self.interpreter.interpret("Hospital Costa Sur: tenemos 3200 galones de oxígeno.", REFERENCE)
        self.assertIsNone(unsupported.facts.current_inventory)
        self.assertTrue(unsupported.unsupported_fragments)

    def test_unassociated_quantity_is_not_inventory(self):
        result = self.interpreter.interpret("Hospital Costa Sur. Oxígeno medicinal. 3200 kg.", REFERENCE)
        self.assertIsNone(result.facts.current_inventory)
        self.assertIsNone(result.facts.consumption_rate)
        self.assertIsNone(result.facts.planned_delivery_quantity)

    def test_unsupported_fragments_are_explicit(self):
        telemetry = self.interpreter.interpret("Hospital Costa Sur: el tanque está al 32%.", REFERENCE)
        self.assertIsNone(telemetry.facts.current_inventory)
        self.assertTrue(telemetry.unsupported_fragments)
        for phrase in ("mañana", "próximo jueves", "final de semana"):
            result = self.interpreter.interpret(f"Hospital Costa Sur: entrega {phrase}.", REFERENCE)
            self.assertIsNone(result.facts.planned_delivery_at)
            self.assertTrue(result.unsupported_fragments)

    def test_unknown_and_ambiguous_identity(self):
        unknown = self.interpreter.interpret("Hospital Norte tiene 3200 kg de oxígeno.", REFERENCE)
        self.assertEqual(unknown.identity.customer.status, "unresolved")
        ambiguous_context = SupplyAssuranceIdentityContext({"Hospital Costa Sur": context().resolve("Hospital Costa Sur"), "Hospital Centro": context().resolve("Hospital Costa Sur")})
        ambiguous = SupplyAssuranceInterpreter(ambiguous_context).interpret("Hospital Costa Sur y Hospital Centro tienen stock.", REFERENCE)
        self.assertEqual(ambiguous.identity.customer.status, "ambiguous")

    def test_identity_entities_resolve_independently(self):
        known_customer = self.interpreter.interpret("Hospital Costa Sur: producto desconocido, stock 3200 kg.", REFERENCE)
        self.assertEqual(known_customer.identity.customer.status, "resolved")
        self.assertEqual(known_customer.identity.gas_product.status, "unresolved")
        self.assertEqual(known_customer.identity.installation.status, "absent")
        known_product = self.interpreter.interpret("Centro desconocido: oxígeno medicinal, stock 3200 kg.", REFERENCE)
        self.assertEqual(known_product.identity.customer.status, "unresolved")
        self.assertEqual(known_product.identity.gas_product.status, "resolved")

    def test_ambiguous_installation_is_not_selected(self):
        base = context().resolve("Hospital Costa Sur")
        context_with_tanks = SupplyAssuranceIdentityContext({
            "Hospital Costa Sur": base,
            "tanque principal A": ResolvedSupplyIdentity(base.customer, base.site, base.application, base.gas_product,
                IdentityReference(SupplyInstallation("tank-a", "site-1", "oxygen", "bulk", "cryogenic_tank", Quantity(10000, "kg")))),
            "tanque principal B": ResolvedSupplyIdentity(base.customer, base.site, base.application, base.gas_product,
                IdentityReference(SupplyInstallation("tank-b", "site-1", "oxygen", "bulk", "cryogenic_tank", Quantity(10000, "kg")))),
        })
        result = SupplyAssuranceInterpreter(context_with_tanks).interpret(
            "Hospital Costa Sur oxígeno medicinal en tanque principal.", REFERENCE)
        self.assertEqual(result.identity.installation.status, "ambiguous")
        self.assertEqual(set(result.identity.installation.candidates), {"tank-a", "tank-b"})

    def test_telemetry_and_relative_time_boundaries(self):
        level = self.interpreter.interpret("Hospital Costa Sur: el tanque está al 32%.", REFERENCE)
        self.assertIsNone(level.facts.current_inventory)
        self.assertEqual(self.interpreter.interpret("Hospital Costa Sur: entrega mañana.", REFERENCE).facts.planned_delivery_at, None)
        self.assertEqual(self.interpreter.interpret("Hospital Costa Sur: entrega el próximo jueves.", REFERENCE).facts.planned_delivery_at, None)

    def test_incompatible_units_are_extracted_without_conversion(self):
        result = self.interpreter.interpret("Hospital Costa Sur: oxígeno medicinal, stock actual 3 t. Consumo diario 700 kg. Entrega 100 kg dentro de 4 días. Stock de seguridad 10 t.", REFERENCE)
        self.assertEqual(result.facts.current_inventory.unit, "t")
        self.assertEqual(result.facts.consumption_rate.quantity_unit, "kg")
        request = SupplyAssuranceRequestComposer().compose(result.facts, result.identity).request
        self.assertEqual(SupplyAssuranceService().assess(request).status, "INVALID")

    def test_provenance_and_no_energy_leakage(self):
        result = self.interpreter.interpret("Hospital Costa Sur: stock actual 3200 kg. Consumo diario 700 kg.", REFERENCE)
        self.assertTrue(all(item.origin == "explicit_input" and item.extractor == "deterministic" for item in result.facts.provenance))
        self.assertFalse(any(token in str(result.facts).lower() for token in ("gwh", "spot", "short", "take_or_pay")))
        with self.assertRaises(ValueError):
            self.interpreter.interpret("Hospital Costa Sur: stock actual 3200 kg.", datetime(2026, 9, 22))
