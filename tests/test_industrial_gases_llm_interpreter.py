from datetime import datetime, timedelta, timezone
import json
from decimal import Decimal
from types import SimpleNamespace
import unittest

from pydantic import ValidationError

from industrial_gases import (
    Application, ApplicationGasRequirement, ConsumptionForecast,
    ConsumptionRate, Customer, DeliveryPlan, ExtractionOperationalError,
    GasProduct, IdentityReference, InventorySnapshot, LLMSupplyAssuranceInterpreter,
    LLMSupplyExtraction, Quantity, ResolvedSupplyIdentity, Site,
    SupplyAssuranceIdentityContext, SupplyAssuranceRequestComposer,
    SupplyAssuranceService, SupplyInstallation,
)
from llm_client import GenerationCancelledError, LLMTimeoutError


REFERENCE = datetime(2026, 9, 22, 10, tzinfo=timezone(timedelta(hours=2)))


def identity_context(ambiguous=False):
    base = ResolvedSupplyIdentity(
        customer=IdentityReference(Customer("customer-1", "Hospital Costa Sur")),
        site=IdentityReference(Site("site-1", "customer-1", "Hospital Costa Sur")),
        application=IdentityReference(Application(
            "app-1", "site-1", "critical oxygen",
            (ApplicationGasRequirement("oxygen", "medical"),),
        )),
        gas_product=IdentityReference(GasProduct("oxygen", "oxígeno medicinal", "liquid")),
        installation=IdentityReference(SupplyInstallation(
            "tank-1", "site-1", "oxygen", "bulk", "cryogenic_tank", Quantity(10000, "kg"),
        )),
    )
    entries = {"Hospital Costa Sur": base}
    if ambiguous:
        for tank_id in ("tank-a", "tank-b"):
            entries[tank_id.replace("tank-", "tanque principal ")] = ResolvedSupplyIdentity(
                base.customer, base.site, base.application, base.gas_product,
                IdentityReference(SupplyInstallation(
                    tank_id, "site-1", "oxygen", "bulk", "cryogenic_tank", Quantity(10000, "kg"),
                )),
            )
    return SupplyAssuranceIdentityContext(entries)


class FakeProvider:
    def __init__(self, content=None, error=None):
        self.content = content
        self.error = error
        self.messages = None
        self.timeout_seconds = None
        self.options = None

    def generate_response(self, messages, timeout_seconds=None, options=None):
        self.messages = messages
        self.timeout_seconds = timeout_seconds
        self.options = options
        if self.error:
            raise self.error
        return SimpleNamespace(content=self.content)


def candidate(value, unit, evidence):
    return {"value": value, "unit": unit, "evidence": evidence}


def complete_payload(unit="kg", inventory="3200", consumption_unit=None):
    consumption_unit = consumption_unit or unit
    return {
        "current_inventory": candidate(inventory, unit, f"nos quedan {inventory} {unit} de oxígeno"),
        "consumption_rate": {
            "value": "700", "unit": consumption_unit, "time_unit": "day",
            "evidence": f"consumimos 700 {consumption_unit} diarios",
        },
        "planned_delivery_quantity": candidate("4000" if unit == "kg" else "4", unit,
            f"la entrega será de {'4000' if unit == 'kg' else '4'} {unit}"),
        "planned_delivery_time": {
            "kind": "relative_days", "value": 4, "evidence": "dentro de 4 días",
        },
        "safety_stock": candidate("1500" if unit == "kg" else "1.5", unit,
            f"la reserva de seguridad es {'1500' if unit == 'kg' else '1.5'} {unit}"),
        "customer_label": "Hospital Costa Sur",
        "gas_product_label": "oxígeno medicinal",
    }


def run_extract(text, payload, context=None, provider=None, **kwargs):
    provider = provider or FakeProvider(json.dumps(payload, ensure_ascii=False))
    interpreter = LLMSupplyAssuranceInterpreter(provider, context or identity_context())
    return interpreter.interpret(text, REFERENCE, **kwargs), provider


class LLMSupplyExtractionSchemaTests(unittest.TestCase):
    def test_valid_dto_optional_fields_and_zero_are_preserved(self):
        value = LLMSupplyExtraction.model_validate({
            "current_inventory": candidate(0, "kg", "stock actual 0 kg"),
        })
        self.assertEqual(value.current_inventory.value, Decimal("0"))
        self.assertIsNone(value.consumption_rate)
        self.assertNotIn("customer_id", LLMSupplyExtraction.model_fields)

    def test_extra_fields_are_rejected_including_identifiers(self):
        for field in (
            "unexpected", "customer_id", "site_id", "application_id", "gas_product_id",
            "installation_id", "planned_delivery_at", "business_status",
        ):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                LLMSupplyExtraction.model_validate({field: "invented"})

    def test_malformed_json_is_operational_error(self):
        with self.assertRaises(ExtractionOperationalError) as caught:
            run_extract("texto", {}, provider=FakeProvider("prosa {no json}"))
        self.assertEqual(caught.exception.code, "invalid_json")

    def test_schema_invalid_json_is_operational_error(self):
        with self.assertRaises(ExtractionOperationalError) as caught:
            run_extract("texto", {}, provider=FakeProvider('{"current_inventory":{"value":1}}'))
        self.assertEqual(caught.exception.code, "schema_invalid")


class LLMSupplyExtractionGroundingTests(unittest.TestCase):
    def test_literal_evidence_and_provenance_are_accepted(self):
        text = "Hospital Costa Sur: nos quedan 3200 kg de oxígeno medicinal."
        payload = {"current_inventory": candidate("3200", "kg", "nos quedan 3200 kg de oxígeno medicinal"),
                   "customer_label": "Hospital Costa Sur", "gas_product_label": "oxígeno medicinal"}
        result, _ = run_extract(text, payload)
        self.assertEqual(result.facts.current_inventory, Quantity(3200, "kg"))
        provenance = result.facts.provenance[0]
        self.assertEqual((provenance.extractor, provenance.origin), ("llm", "explicit_input"))
        self.assertEqual(provenance.source_text, "nos quedan 3200 kg de oxígeno medicinal")

    def test_nonexistent_evidence_is_rejected(self):
        text = "Stock actual: 3200 kg."
        payload = {"current_inventory": candidate("3200", "kg", "Tenemos 3200 kg")}
        result, _ = run_extract(text, payload)
        self.assertIsNone(result.facts.current_inventory)
        self.assertEqual([(i.field, i.code) for i in result.issues], [("current_inventory", "evidence_not_found")])

    def test_invented_number_is_rejected(self):
        text = "Nos quedan 3200 kg de oxígeno."
        payload = {"current_inventory": candidate("3300", "kg", text)}
        result, _ = run_extract(text, payload)
        self.assertIsNone(result.facts.current_inventory)
        self.assertEqual(result.issues[0].code, "number_mismatch")

    def test_invented_or_mismatched_unit_is_rejected_without_conversion(self):
        text = "Nos quedan 3,2 t de oxígeno."
        payload = {"current_inventory": candidate("3.2", "kg", text)}
        result, _ = run_extract(text, payload)
        self.assertIsNone(result.facts.current_inventory)
        self.assertEqual(result.issues[0].code, "unit_mismatch")
        unsupported_text = "Nos quedan 5 gallons de oxígeno."
        unsupported = {"current_inventory": candidate("5", "gallons", unsupported_text)}
        result, _ = run_extract(unsupported_text, unsupported)
        self.assertIsNone(result.facts.current_inventory)
        self.assertEqual(result.issues[0].code, "unsupported_unit")

    def test_thousands_of_kg_is_not_inferred_from_3_2_t_evidence(self):
        text = "Nos quedan 3,2 t de oxígeno medicinal"
        payload = {"current_inventory": candidate("3200", "kg", "3,2 t")}
        result, _ = run_extract(text, payload)
        self.assertIsNone(result.facts.current_inventory)
        self.assertNotIn(Quantity(3200, "kg"), (result.facts.current_inventory,))
        self.assertEqual(
            [(issue.field, issue.code) for issue in result.issues],
            [("current_inventory", "number_mismatch")],
        )

    def test_wrong_field_evidence_is_rejected(self):
        text = "Consumimos 700 kg diarios."
        payload = {"current_inventory": candidate("700", "kg", text)}
        result, _ = run_extract(text, payload)
        self.assertIsNone(result.facts.current_inventory)
        self.assertEqual(result.issues[0].code, "field_evidence_mismatch")

    def test_ambiguous_numeric_evidence_is_rejected(self):
        text = "Nos quedan 3,2 t y consumimos 700 kg diarios."
        payload = {"current_inventory": candidate("3.2", "t", text)}
        result, _ = run_extract(text, payload)
        self.assertIsNone(result.facts.current_inventory)
        self.assertEqual(result.issues[0].code, "ambiguous_numeric_evidence")

    def test_independent_valid_candidates_survive_invalid_candidate(self):
        text = "Nos quedan 3200 kg de oxígeno, consumimos 700 kg diarios y la entrega será de 4000 kg."
        payload = {
            "current_inventory": candidate("3200", "kg", "Nos quedan 3200 kg de oxígeno"),
            "consumption_rate": {"value": "701", "unit": "kg", "time_unit": "day",
                                 "evidence": "consumimos 700 kg diarios"},
            "planned_delivery_quantity": candidate("4000", "kg", "la entrega será de 4000 kg"),
        }
        result, _ = run_extract(text, payload)
        self.assertEqual(result.facts.current_inventory, Quantity(3200, "kg"))
        self.assertIsNone(result.facts.consumption_rate)
        self.assertEqual(result.facts.planned_delivery_quantity, Quantity(4000, "kg"))
        self.assertEqual(result.issues[0].field, "consumption_rate")


class LLMSupplyExtractionLexicalTests(unittest.TestCase):
    def test_numeric_formats_are_grounded_as_decimal(self):
        examples = (("3200", "3200"), ("3.200", "3200"), ("3 200", "3200"),
                    ("3,2", "3.2"), ("3.2", "3.2"))
        for lexical, expected in examples:
            with self.subTest(lexical=lexical):
                text = f"Nos quedan {lexical} kg de oxígeno."
                payload = {"current_inventory": candidate(expected, "kg", f"Nos quedan {lexical} kg de oxígeno.")}
                result, _ = run_extract(text, payload)
                self.assertEqual(result.facts.current_inventory.value, Decimal(expected))

    def test_catalog_units_and_written_aliases_are_normalized_without_conversion(self):
        examples = (("kg", "kg", "3.2"), ("t", "t", "3.2"), ("L", "L", "3.2"),
                    ("m3", "m3", "3.2"), (f"m{chr(0x00b3)}", "m3", "3.2"),
                    ("Nm3", "Nm3", "3.2"), (f"Nm{chr(0x00b3)}", "Nm3", "3.2"),
                    ("Sm3", "Sm3", "3.2"), (f"Sm{chr(0x00b3)}", "Sm3", "3.2"),
                    (f"m3{chr(0x00b3)}", "m3", "3.2"),
                    (f"Nm3{chr(0x00b3)}", "Nm3", "3.2"),
                    (f"Sm3{chr(0x00b3)}", "Sm3", "3.2"),
                    (f"nm3{chr(0x00b3)}", "Nm3", "3.2"),
                    (f"sm3{chr(0x00b3)}", "Sm3", "3.2"),
                    ("toneladas", "t", "3.2"), ("litros", "L", "3.2"))
        for source_unit, normalized, value in examples:
            with self.subTest(source_unit=source_unit):
                text = f"Nos quedan {value} {source_unit} de oxígeno."
                payload = {"current_inventory": candidate(value, normalized, text)}
                result, _ = run_extract(text, payload)
                self.assertEqual(result.facts.current_inventory, Quantity(Decimal(value), normalized))
                self.assertEqual(result.facts.provenance[0].source_text, text)

    def test_rate_and_quantity_units_are_not_converted(self):
        text = "Nos quedan 3,2 toneladas de oxígeno y estamos gastando 700 kg diarios."
        payload = {
            "current_inventory": candidate("3.2", "t", "Nos quedan 3,2 toneladas de oxígeno"),
            "consumption_rate": {"value": "700", "unit": "kg", "time_unit": "day",
                                 "evidence": "estamos gastando 700 kg diarios"},
        }
        result, _ = run_extract(text, payload)
        self.assertEqual(result.facts.current_inventory, Quantity("3.2", "t"))
        self.assertEqual(result.facts.consumption_rate, ConsumptionRate(700, "kg", "day"))

    def test_rate_provenance_preserves_original_nm3_spelling(self):
        rate_phrase = f"700 Nm{chr(0x00b3)} al día"
        evidence = f"consumimos {rate_phrase}"
        result, _ = run_extract(
            evidence,
            {"consumption_rate": {
                "value": "700", "unit": "Nm3", "time_unit": "day", "evidence": evidence,
            }},
        )
        self.assertEqual(result.facts.consumption_rate, ConsumptionRate(700, "Nm3", "day"))
        provenance = result.facts.provenance[0]
        self.assertEqual(provenance.source_text, evidence)
        self.assertIn(rate_phrase, provenance.source_text)
        self.assertEqual((provenance.origin, provenance.extractor), ("explicit_input", "llm"))


class LLMSupplyExtractionTimeIdentityTests(unittest.TestCase):
    def test_relative_days_uses_explicit_reference_time(self):
        text = "La entrega será dentro de 4 días."
        payload = {"planned_delivery_time": {"kind": "relative_days", "value": 4,
                                               "evidence": "dentro de 4 días"}}
        result, _ = run_extract(text, payload)
        self.assertEqual(result.facts.planned_delivery_at, REFERENCE + timedelta(days=4))
        self.assertEqual(result.facts.provenance[0].source_text, "dentro de 4 días")

    def test_unsupported_relative_expression_and_naive_reference_are_rejected(self):
        text = "La entrega será mañana."
        payload = {"planned_delivery_time": {"kind": "tomorrow", "value": 1, "evidence": "mañana"}}
        result, _ = run_extract(text, payload)
        self.assertIsNone(result.facts.planned_delivery_at)
        self.assertEqual(result.issues[0].code, "unsupported_temporal_expression")
        interpreter = LLMSupplyAssuranceInterpreter(FakeProvider("{}"), identity_context())
        with self.assertRaises(ValueError):
            interpreter.interpret("texto", datetime(2026, 9, 22))

    def test_labels_resolve_known_and_unknown_without_ids(self):
        text = "Hospital Costa Sur usa oxígeno medicinal."
        payload = {"customer_label": "Hospital Costa Sur", "gas_product_label": "oxígeno medicinal"}
        result, _ = run_extract(text, payload)
        self.assertEqual(result.identity.customer.status, "resolved")
        self.assertEqual(result.identity.gas_product.status, "resolved")
        self.assertEqual(result.identity.installation.status, "resolved")
        unknown = {"customer_label": "Hospital Norte", "gas_product_label": "argón medicinal"}
        result, _ = run_extract("Hospital Norte utiliza argón medicinal.", unknown)
        self.assertEqual(result.identity.customer.status, "unresolved")
        self.assertEqual(result.identity.gas_product.status, "unresolved")

    def test_omitted_identity_label_remains_absent(self):
        result, _ = run_extract("Hospital Costa Sur usa oxígeno medicinal.", {})
        self.assertEqual(result.identity.customer.status, "absent")
        self.assertIsNone(result.identity.customer.value)
        self.assertIsNone(result.identity.customer.label)

    def test_ambiguous_installation_remains_ambiguous(self):
        text = "Hospital Costa Sur oxígeno medicinal tanque principal."
        payload = {"customer_label": "Hospital Costa Sur", "gas_product_label": "oxígeno medicinal",
                   "installation_label": "tanque principal"}
        result, _ = run_extract(text, payload, context=identity_context(ambiguous=True))
        self.assertEqual(result.identity.installation.status, "ambiguous")
        self.assertEqual(set(result.identity.installation.candidates), {"tank-a", "tank-b"})


class LLMSupplyExtractionBoundaryAndE2ETests(unittest.TestCase):
    def test_messages_are_role_separated_and_provider_tools_are_disabled(self):
        text = "Hospital Costa Sur: nos quedan 3200 kg."
        result, provider = run_extract(text, {})
        self.assertEqual([m["role"] for m in provider.messages], ["system", "user"])
        self.assertNotIn(text, provider.messages[0]["content"])
        system_content = " ".join(provider.messages[0]["content"].split())
        for instruction in ("Extract only facts", "Do not calculate", "Do not convert units",
                            "Do not infer missing values", "Do not invent IDs",
                            "Preserve the unit", "literal evidence", "JSON Schema"):
            self.assertIn(instruction, system_content)
        self.assertIn("current_inventory", system_content)
        self.assertEqual(json.loads(provider.messages[1]["content"]), {"input_text": text})
        self.assertFalse(provider.options.tool_calling_enabled)
        self.assertEqual(provider.options.max_rounds, 1)
        self.assertEqual(provider.options.trace_purpose, "industrial_gases_extraction")
        self.assertFalse(hasattr(result, "projection"))
        self.assertEqual(result.issues, ())

    def test_timeout_and_provider_failures_remain_operational_errors(self):
        for error, expected in ((LLMTimeoutError("timeout"), "timeout"),
                                (ConnectionError("offline"), "provider_failure")):
            with self.subTest(code=expected), self.assertRaises(ExtractionOperationalError) as caught:
                run_extract("texto", {}, provider=FakeProvider(error=error))
            self.assertEqual(caught.exception.code, expected)
        with self.assertRaises(GenerationCancelledError):
            run_extract("texto", {}, provider=FakeProvider(error=GenerationCancelledError()))
        with self.assertRaises(TypeError):
            run_extract("texto", {}, provider=FakeProvider(error=TypeError("provider implementation bug")))

    def test_all_ungrounded_candidates_remain_issues_without_fallback(self):
        text = "Nos quedan 3200 kg; consumimos 700 kg diarios; entrega será de 4000 kg."
        payload = {
            "current_inventory": candidate("3201", "kg", "Nos quedan 3200 kg"),
            "consumption_rate": {
                "value": "701", "unit": "kg", "time_unit": "day",
                "evidence": "consumimos 700 kg diarios",
            },
            "planned_delivery_quantity": candidate("4001", "kg", "entrega será de 4000 kg"),
        }
        result, provider = run_extract(text, payload)
        self.assertEqual(len(provider.messages), 2)
        self.assertIsNone(result.facts.current_inventory)
        self.assertIsNone(result.facts.consumption_rate)
        self.assertIsNone(result.facts.planned_delivery_quantity)
        self.assertEqual(
            [(issue.field, issue.code) for issue in result.issues],
            [("current_inventory", "number_mismatch"),
             ("consumption_rate", "number_mismatch"),
             ("planned_delivery_quantity", "number_mismatch")],
        )

    def test_natural_language_complete_case_flows_to_deterministic_service(self):
        text = ("Hospital Costa Sur: nos quedan 3200 kg de oxígeno medicinal; "
                "consumimos 700 kg diarios; la entrega será de 4000 kg dentro de 4 días; "
                "la reserva de seguridad es 1500 kg.")
        result, _ = run_extract(text, complete_payload())
        self.assertEqual(result.facts.current_inventory, Quantity(3200, "kg"))
        composed = SupplyAssuranceRequestComposer().compose(result.facts, result.identity)
        assessed = SupplyAssuranceService().assess(composed.request)
        self.assertEqual(assessed.status, "COMPLETED")
        self.assertEqual(assessed.projection.inventory_immediately_before_delivery, Quantity(400, "kg"))
        self.assertEqual(assessed.projection.safety_stock_gap_before_delivery.value, Decimal("-1100"))

    def test_incompatible_units_are_extracted_then_rejected_by_domain(self):
        text = ("Hospital Costa Sur: nos quedan 3,2 toneladas de oxígeno medicinal; "
                "consumimos 700 kg diarios; la entrega será de 4 toneladas dentro de 4 días; "
                "la reserva de seguridad es 1,5 toneladas.")
        payload = complete_payload("t", "3.2", "kg")
        payload["current_inventory"]["evidence"] = "nos quedan 3,2 toneladas de oxígeno medicinal"
        payload["planned_delivery_quantity"]["evidence"] = "la entrega será de 4 toneladas"
        payload["planned_delivery_quantity"]["value"] = "4"
        payload["planned_delivery_quantity"]["unit"] = "t"
        payload["safety_stock"]["evidence"] = "la reserva de seguridad es 1,5 toneladas"
        payload["safety_stock"]["value"] = "1.5"
        payload["safety_stock"]["unit"] = "t"
        payload["consumption_rate"]["evidence"] = "consumimos 700 kg diarios"
        result, _ = run_extract(text, payload)
        self.assertEqual(result.facts.current_inventory, Quantity("3.2", "t"))
        self.assertEqual(result.facts.consumption_rate.quantity_unit, "kg")
        composed = SupplyAssuranceRequestComposer().compose(result.facts, result.identity)
        self.assertEqual(SupplyAssuranceService().assess(composed.request).status, "INVALID")

    def test_missing_facts_remain_service_owned(self):
        text = "Hospital Costa Sur: nos quedan 3200 kg de oxígeno medicinal."
        payload = {"current_inventory": candidate("3200", "kg", "nos quedan 3200 kg de oxígeno medicinal"),
                   "customer_label": "Hospital Costa Sur", "gas_product_label": "oxígeno medicinal"}
        result, _ = run_extract(text, payload)
        composed = SupplyAssuranceRequestComposer().compose(result.facts, result.identity)
        assessed = SupplyAssuranceService().assess(composed.request)
        self.assertEqual(assessed.status, "MISSING_INPUTS")
        self.assertIsNone(assessed.projection)


if __name__ == "__main__":
    unittest.main()
