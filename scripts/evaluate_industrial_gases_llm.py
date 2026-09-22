"""Manual comparison of deterministic and LLM Industrial Gases extraction.

PowerShell (no generation calls):
    python scripts/evaluate_industrial_gases_llm.py --deterministic-only

LM Studio uses the existing LLM_PROVIDER / LMSTUDIO_BASE_URL / LMSTUDIO_MODEL
configuration. Select it explicitly with --provider lmstudio; --model overrides
the configured model for this run only. No environment file is modified.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import sys
from time import perf_counter
from urllib.parse import urlsplit, urlunsplit


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from industrial_gases import (  # noqa: E402
    Application,
    ApplicationGasRequirement,
    ConsumptionForecast,
    ConsumptionRate,
    Customer,
    DeliveryPlan,
    GasProduct,
    IdentityReference,
    InventorySnapshot,
    Quantity,
    ResolvedSupplyIdentity,
    Site,
    SupplyAssuranceIdentityContext,
    SupplyAssuranceInterpreter,
    SupplyAssuranceRequestComposer,
    SupplyAssuranceService,
    SupplyInstallation,
    ExtractionOperationalError,
)
from runtime_config import LLMRuntimeConfig  # noqa: E402


REFERENCE_TIME = datetime(2026, 9, 22, 12, tzinfo=timezone(timedelta(hours=2)))
CATEGORIES = (
    "structured_easy", "natural_phrasing", "missing_facts",
    "unsupported_ambiguous", "typographic_units", "identity",
)


@dataclass(frozen=True)
class ExpectedQuantity:
    value: Decimal
    unit: str

    def __post_init__(self):
        object.__setattr__(self, "value", Decimal(str(self.value)))


@dataclass(frozen=True)
class ExpectedRate:
    value: Decimal
    unit: str
    time_unit: str = "day"

    def __post_init__(self):
        object.__setattr__(self, "value", Decimal(str(self.value)))


@dataclass(frozen=True)
class ExpectedFacts:
    current_inventory: ExpectedQuantity | None = None
    consumption_rate: ExpectedRate | None = None
    planned_delivery_quantity: ExpectedQuantity | None = None
    planned_delivery_in_days: int | None = None
    safety_stock: ExpectedQuantity | None = None
    customer_label: str | None = None
    site_label: str | None = None
    application_label: str | None = None
    gas_product_label: str | None = None
    installation_label: str | None = None


@dataclass(frozen=True)
class EvaluationCase:
    id: str
    category: str
    text: str
    expected: ExpectedFacts
    reference_time: datetime = REFERENCE_TIME
    expected_service_status: str | None = None
    expected_identity_status: tuple[tuple[str, str], ...] = ()
    ambiguous_installation_context: bool = False


Q = ExpectedQuantity
R = ExpectedRate
CASES = (
    EvaluationCase(
    "IG-01", "structured_easy",
        "Hospital Costa Sur. Tenemos 3200 kg de oxígeno medicinal. "
        "Consumimos 700 kg al día. Entrega de 4000 kg dentro de 4 días. "
        "Stock de seguridad 1500 kg.",
        ExpectedFacts(Q(3200, "kg"), R(700, "kg"), Q(4000, "kg"), 4,
                      Q(1500, "kg"), "Hospital Costa Sur", None, None,
                      "oxígeno medicinal"),
        expected_service_status="COMPLETED",
    ),
    EvaluationCase(
        "IG-02", "structured_easy",
        "Hospital Costa Sur. Stock actual: 3.200 kg de oxígeno medicinal. "
        "Consumo diario: 700 kg. Entrega prevista: 4.000 kg dentro de 4 días. "
        "Stock de seguridad: 1.500 kg.",
        ExpectedFacts(Q(3200, "kg"), R(700, "kg"), Q(4000, "kg"), 4,
                      Q(1500, "kg"), "Hospital Costa Sur", None, None,
                      "oxígeno medicinal"),
        expected_service_status="COMPLETED",
    ),
    EvaluationCase(
        "IG-03", "natural_phrasing",
        "En el Hospital Costa Sur nos quedan unas 3,2 toneladas de oxígeno "
        "medicinal y estamos gastando 700 kilos al día. El próximo suministro "
        "será de 4 toneladas dentro de 4 días y queremos mantener 1,5 toneladas "
        "de reserva.",
        ExpectedFacts(Q("3.2", "t"), R(700, "kg"), Q(4, "t"), 4,
                      Q("1.5", "t"), "Hospital Costa Sur", None, None,
                      "oxígeno medicinal"),
        expected_service_status="INVALID",
    ),
    EvaluationCase(
        "IG-04", "natural_phrasing",
        "Hospital Costa Sur: disponemos de 2800 kg de oxígeno medicinal; la "
        "previsión indica un consumo de 600 kg diarios; recibiremos 2000 kg "
        "dentro de 3 días y queremos conservar 500 kg de reserva.",
        ExpectedFacts(Q(2800, "kg"), R(600, "kg"), Q(2000, "kg"), 3,
                      Q(500, "kg"), "Hospital Costa Sur", None, None,
                      "oxígeno medicinal"),
        expected_service_status="COMPLETED",
    ),
    EvaluationCase(
        "IG-05", "natural_phrasing",
        "En Hospital Costa Sur el inventario disponible ronda los 2,4 t de "
        "oxígeno medicinal. Se consumen 500 kg por día; la entrega de 3 t "
        "llegará dentro de 5 días y la reserva mínima es de 0,8 t.",
        ExpectedFacts(Q("2.4", "t"), R(500, "kg"), Q(3, "t"), 5,
                      Q("0.8", "t"), "Hospital Costa Sur", None, None,
                      "oxígeno medicinal"),
        expected_service_status="INVALID",
    ),
    EvaluationCase(
        "IG-06", "missing_facts",
        "Hospital Costa Sur. Tenemos 3200 kg de oxígeno medicinal.",
        ExpectedFacts(Q(3200, "kg"), customer_label="Hospital Costa Sur",
                      gas_product_label="oxígeno medicinal"),
        expected_service_status="MISSING_INPUTS",
    ),
    EvaluationCase(
        "IG-07", "missing_facts",
        "En Hospital Costa Sur quedan 900 kg de oxígeno medicinal y se consumen "
        "120 kg diarios.",
        ExpectedFacts(Q(900, "kg"), R(120, "kg"),
                      customer_label="Hospital Costa Sur",
                      gas_product_label="oxígeno medicinal"),
        expected_service_status="MISSING_INPUTS",
    ),
    EvaluationCase(
        "IG-08", "unsupported_ambiguous",
        "Hospital Costa Sur: el tanque está al 32%.",
        ExpectedFacts(customer_label="Hospital Costa Sur"),
        expected_identity_status=(("customer", "resolved"),),
    ),
    EvaluationCase(
        "IG-09", "unsupported_ambiguous",
        "El camión con oxígeno para Hospital Costa Sur llegará mañana.",
        ExpectedFacts(customer_label="Hospital Costa Sur",
                      gas_product_label="oxígeno medicinal"),
        expected_identity_status=(("customer", "resolved"), ("gas_product", "resolved")),
    ),
    EvaluationCase(
        "IG-10", "unsupported_ambiguous",
        "Hospital Costa Sur: nos quedan unos 3000 o 3500 kg de oxígeno medicinal.",
        ExpectedFacts(customer_label="Hospital Costa Sur",
                      gas_product_label="oxígeno medicinal"),
        expected_identity_status=(("customer", "resolved"), ("gas_product", "resolved")),
    ),
    EvaluationCase(
        "IG-11", "typographic_units",
        f"Hospital Costa Sur: stock actual de oxígeno 2,5 m{chr(0x00b3)}.",
        ExpectedFacts(Q("2.5", "m3"), customer_label="Hospital Costa Sur",
                      gas_product_label="oxígeno medicinal"),
    ),
    EvaluationCase(
        "IG-12", "typographic_units",
        f"Hospital Costa Sur: consumimos 700 Nm{chr(0x00b3)} al día.",
        ExpectedFacts(consumption_rate=R(700, "Nm3"),
                      customer_label="Hospital Costa Sur",
                      gas_product_label="oxígeno medicinal"),
    ),
    EvaluationCase(
        "IG-13", "typographic_units",
        f"Hospital Costa Sur: stock actual de oxígeno 180 Sm{chr(0x00b3)}. "
        f"El stock de seguridad es 20 Sm3{chr(0x00b3)}.",
        ExpectedFacts(Q(180, "Sm3"), safety_stock=Q(20, "Sm3"),
                      customer_label="Hospital Costa Sur",
                      gas_product_label="oxígeno medicinal"),
    ),
    EvaluationCase(
        "IG-14", "identity",
        "Hospital Costa Sur registra 850 kg de argón medicinal.",
        ExpectedFacts(Q(850, "kg"), customer_label="Hospital Costa Sur",
                      gas_product_label="argón medicinal"),
        expected_identity_status=(("customer", "resolved"), ("gas_product", "unresolved")),
    ),
    EvaluationCase(
        "IG-15", "identity",
        "Hospital Costa Sur, oxígeno medicinal, en el tanque principal.",
        ExpectedFacts(customer_label="Hospital Costa Sur",
                      gas_product_label="oxígeno medicinal"),
        expected_identity_status=(("customer", "resolved"), ("installation", "ambiguous")),
        ambiguous_installation_context=True,
    ),
)


FACT_FIELDS = (
    "current_inventory", "consumption_rate", "planned_delivery_quantity",
    "planned_delivery_in_days", "safety_stock",
)
IDENTITY_LABEL_FIELDS = (
    "customer_label", "site_label", "application_label",
    "gas_product_label", "installation_label",
)
COMPARISON_STATUSES = ("MATCH", "MISSING", "UNEXPECTED", "MISMATCH")


def filter_cases(cases=CASES, case_id: str | None = None,
                 category: str | None = None) -> tuple[EvaluationCase, ...]:
    selected = tuple(case for case in cases
                     if (case_id is None or case.id == case_id)
                     and (category is None or case.category == category))
    if case_id is not None and not any(case.id == case_id for case in cases):
        raise ValueError(f"Unknown case id: {case_id}")
    if category is not None and category not in {case.category for case in cases}:
        raise ValueError(f"Unknown category: {category}")
    return selected


def _reference_identity(ambiguous_installations: bool = False) -> SupplyAssuranceIdentityContext:
    customer = Customer("customer-hcs", "Hospital Costa Sur")
    site = Site("site-hcs", customer.customer_id, "Hospital Costa Sur")
    application = Application(
        "application-oxygen", site.site_id, "Medicinal oxygen",
        (ApplicationGasRequirement("oxygen-medical", "medical_supply"),),
    )
    product = GasProduct("oxygen-medical", "oxígeno medicinal", "liquid")
    installation = SupplyInstallation(
        "installation-oxygen-main", site.site_id, product.gas_product_id,
        "bulk", "cryogenic_tank", Quantity(10000, "kg"),
    )
    base = ResolvedSupplyIdentity(
        IdentityReference(customer), IdentityReference(site),
        IdentityReference(application), IdentityReference(product),
        IdentityReference(installation),
    )
    entries = {"Hospital Costa Sur": base}
    if ambiguous_installations:
        for suffix in ("A", "B"):
            tank = SupplyInstallation(
                f"installation-oxygen-{suffix.lower()}", site.site_id,
                product.gas_product_id, "bulk", "cryogenic_tank", Quantity(10000, "kg"),
            )
            entries[f"tanque principal {suffix}"] = ResolvedSupplyIdentity(
                IdentityReference(customer), IdentityReference(site),
                IdentityReference(application), IdentityReference(product),
                IdentityReference(tank),
            )
    return SupplyAssuranceIdentityContext(entries)


def _quantity_value(value: Quantity | None) -> dict | None:
    return None if value is None else {"value": str(value.value), "unit": value.unit}


def _rate_value(value: ConsumptionRate | None) -> dict | None:
    return None if value is None else {
        "value": str(value.value), "unit": value.quantity_unit,
        "time_unit": value.time_unit,
    }


def _identity_label(reference: IdentityReference) -> str | None:
    if reference.value is not None:
        return getattr(reference.value, "name", None)
    return reference.label


def facts_view(facts, reference_time: datetime) -> dict:
    delivery_days = None
    if facts.planned_delivery_at is not None:
        duration = facts.planned_delivery_at - reference_time
        delivery_days = duration.total_seconds() / 86400
        if delivery_days.is_integer():
            delivery_days = int(delivery_days)
    return {
        "current_inventory": _quantity_value(facts.current_inventory),
        "consumption_rate": _rate_value(facts.consumption_rate),
        "planned_delivery_quantity": _quantity_value(facts.planned_delivery_quantity),
        "planned_delivery_in_days": delivery_days,
        "safety_stock": _quantity_value(facts.safety_stock),
    }


def expected_view(expected: ExpectedFacts) -> dict:
    return {
        "current_inventory": None if expected.current_inventory is None else {
            "value": str(expected.current_inventory.value), "unit": expected.current_inventory.unit,
        },
        "consumption_rate": None if expected.consumption_rate is None else {
            "value": str(expected.consumption_rate.value), "unit": expected.consumption_rate.unit,
            "time_unit": expected.consumption_rate.time_unit,
        },
        "planned_delivery_quantity": None if expected.planned_delivery_quantity is None else {
            "value": str(expected.planned_delivery_quantity.value), "unit": expected.planned_delivery_quantity.unit,
        },
        "planned_delivery_in_days": expected.planned_delivery_in_days,
        "safety_stock": None if expected.safety_stock is None else {
            "value": str(expected.safety_stock.value), "unit": expected.safety_stock.unit,
        },
        **{name: getattr(expected, name) for name in IDENTITY_LABEL_FIELDS},
    }


def compare_facts(expected: ExpectedFacts, facts, identity,
                  reference_time: datetime,
                  expected_identity_status: tuple[tuple[str, str], ...] = ()) -> dict[str, str]:
    actual = facts_view(facts, reference_time)
    expected_values = expected_view(expected)
    comparisons: dict[str, str] = {}
    for name in FACT_FIELDS:
        wanted = getattr(expected, name)
        observed = getattr(facts, {
            "current_inventory": "current_inventory",
            "consumption_rate": "consumption_rate",
            "planned_delivery_quantity": "planned_delivery_quantity",
            "planned_delivery_in_days": "planned_delivery_at",
            "safety_stock": "safety_stock",
        }[name])
        if wanted is None:
            comparisons[name] = "MATCH" if observed is None else "UNEXPECTED"
        elif observed is None:
            comparisons[name] = "MISSING"
        elif isinstance(wanted, ExpectedQuantity):
            comparisons[name] = "MATCH" if (
                observed.unit == wanted.unit and observed.value == wanted.value
            ) else "MISMATCH"
        elif isinstance(wanted, ExpectedRate):
            comparisons[name] = "MATCH" if (
                observed.quantity_unit == wanted.unit
                and observed.time_unit == wanted.time_unit
                and observed.value == wanted.value
            ) else "MISMATCH"
        elif name == "planned_delivery_in_days":
            elapsed_days = Decimal(str((observed - reference_time).total_seconds())) / Decimal(86400)
            comparisons[name] = "MATCH" if elapsed_days == Decimal(wanted) else "MISMATCH"
        else:
            comparisons[name] = "MATCH" if expected_values[name] == actual[name] else "MISMATCH"

    references = {
        "customer_label": identity.customer,
        "site_label": identity.site,
        "application_label": identity.application,
        "gas_product_label": identity.gas_product,
        "installation_label": identity.installation,
    }
    for name in IDENTITY_LABEL_FIELDS:
        wanted = getattr(expected, name)
        if wanted is None:
            continue
        observed = _identity_label(references[name])
        if observed is None:
            comparisons[name] = "MISSING"
        else:
            comparisons[name] = "MATCH" if wanted == observed else "MISMATCH"
    for name, wanted_status in expected_identity_status:
        observed_status = getattr(identity, name).status
        comparisons[f"{name}_identity_status"] = (
            "MATCH" if wanted_status == observed_status else "MISMATCH"
        )
    return comparisons


def comparison_counts(comparisons: dict[str, str]) -> dict[str, int]:
    names = {"MATCH": "matched", "MISSING": "missing",
             "UNEXPECTED": "unexpected", "MISMATCH": "mismatched"}
    return {names[status]: sum(value == status for value in comparisons.values())
            for status in COMPARISON_STATUSES}


def _identity_view(identity) -> dict:
    result = {}
    for name in ("customer", "site", "application", "gas_product", "installation"):
        reference = getattr(identity, name)
        result[name] = {
            "status": reference.status,
            "label": _identity_label(reference),
            "candidates": list(reference.candidates),
        }
    return result


def _service_view(facts, identity, reference_time: datetime) -> dict:
    composition = SupplyAssuranceRequestComposer().compose(facts, identity)
    if composition.request is None:
        return {
            "state": "NOT_RUN",
            "reason": "required identities are unresolved, absent, or ambiguous",
            "missing_identities": list(composition.missing_identities),
            "unresolved_identities": list(composition.unresolved_identities),
            "ambiguous_identities": list(composition.ambiguous_identities),
        }
    result = SupplyAssuranceService().assess(composition.request)
    view = {
        "state": result.status,
        "missing_inputs": list(result.missing_inputs),
        "validation_errors": list(result.validation_errors),
    }
    if result.projection is not None:
        projection = result.projection
        view["projection"] = {
            "days_of_supply": None if projection.days_of_supply is None else str(projection.days_of_supply),
            "inventory_immediately_before_delivery": _quantity_value(projection.inventory_immediately_before_delivery),
            "safety_stock_gap_before_delivery": {
                "value": str(projection.safety_stock_gap_before_delivery.value),
                "unit": projection.safety_stock_gap_before_delivery.unit,
            },
            "stockout_before_delivery": projection.stockout_before_delivery,
            "inventory_immediately_after_delivery": _quantity_value(projection.inventory_immediately_after_delivery),
            "capacity_exceeded": projection.capacity_exceeded,
            "capacity_overflow": _quantity_value(projection.capacity_overflow),
        }
    return view


def _run_one(case: EvaluationCase, interpreter) -> dict:
    started = perf_counter()
    try:
        result = interpreter.interpret(case.text, case_reference_time(case))
    except ExtractionOperationalError as error:
        elapsed_ms = (perf_counter() - started) * 1000
        return {
            "operational_error": {"type": type(error).__name__, "code": error.code,
                                  "message": str(error)},
            "latency_ms": elapsed_ms,
            "grounding_issues": [],
        }
    elapsed_ms = (perf_counter() - started) * 1000
    comparisons = compare_facts(
        case.expected, result.facts, result.identity, case_reference_time(case),
        case.expected_identity_status,
    )
    service = _service_view(result.facts, result.identity, case_reference_time(case))
    return {
        "facts": facts_view(result.facts, case_reference_time(case)),
        "identity": _identity_view(result.identity),
        "unsupported_fragments": list(result.unsupported_fragments),
        "grounding_issues": [asdict(issue) for issue in result.issues],
        "operational_error": None,
        "comparison": comparisons,
        "comparison_counts": comparison_counts(comparisons),
        "service": service,
        "service_status_comparison": (
            "MATCH" if service["state"] == case.expected_service_status else
            ("MISSING" if service["state"] == "NOT_RUN" else "MISMATCH")
        ) if case.expected_service_status is not None else None,
        "latency_ms": elapsed_ms,
    }


def case_reference_time(_case: EvaluationCase) -> datetime:
    return _case.reference_time


def evaluate_cases(cases: tuple[EvaluationCase, ...], deterministic_only: bool = False,
                   llm_only: bool = False, provider=None) -> list[dict]:
    if deterministic_only and llm_only:
        raise ValueError("--deterministic-only and --llm-only cannot be combined")
    if not deterministic_only:
        from industrial_gases import LLMSupplyAssuranceInterpreter

    records = []
    for case in cases:
        record = {
            "id": case.id,
            "category": case.category,
            "text": case.text,
            "reference_time": case.reference_time.isoformat(),
            "expected": expected_view(case.expected),
            "expected_service_status": case.expected_service_status,
            "expected_identity_status": dict(case.expected_identity_status),
        }
        context = _reference_identity(case.ambiguous_installation_context)
        if not llm_only:
            deterministic = SupplyAssuranceInterpreter(context)
            record["deterministic"] = _run_one(case, deterministic)
        if not deterministic_only:
            if provider is None:
                raise ValueError("An LLM provider is required unless --deterministic-only is set")
            llm = LLMSupplyAssuranceInterpreter(provider, context)
            record["llm"] = _run_one(case, llm)
        records.append(record)
    return records


def summarize(records: list[dict], interpreter_names: tuple[str, ...]) -> dict[str, dict[str, float | int]]:
    summary = {}
    for name in interpreter_names:
        rows = [record[name] for record in records if name in record]
        aggregate = {key: 0 for key in ("matched", "missing", "unexpected", "mismatched")}
        latencies = []
        issue_count = 0
        operational_errors = 0
        for row in rows:
            counts = row.get("comparison_counts", {})
            for key in aggregate:
                aggregate[key] += counts.get(key, 0)
            latencies.append(row["latency_ms"])
            issue_count += len(row.get("grounding_issues", ()))
            operational_errors += row.get("operational_error") is not None
        summary[name] = {
            "cases": len(rows), **aggregate,
            "grounding_issues": issue_count,
            "operational_errors": operational_errors,
            "average_latency_ms": sum(latencies) / len(latencies) if latencies else 0.0,
            "total_latency_ms": sum(latencies),
        }
    return summary


def _print_value(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, dict):
        if "value" in value and "unit" in value:
            suffix = f"/{value['time_unit']}" if "time_unit" in value else ""
            return f"{value['value']} {value['unit']}{suffix}"
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def print_case(record: dict) -> None:
    print("\n" + "=" * 60)
    print(f"CASE {record['id']} — {record['category']}")
    print("=" * 60 + "\nINPUT\n" + record["text"])
    print("\nEXPECTED")
    for name, value in record["expected"].items():
        if value is not None:
            print(f"{name}: {_print_value(value)}")
    if record.get("expected_service_status"):
        print(f"service status: {record['expected_service_status']} (expected)")
    for interpreter_name in ("deterministic", "llm"):
        if interpreter_name not in record:
            continue
        result = record[interpreter_name]
        print(f"\n{interpreter_name.upper()}")
        if result.get("operational_error"):
            print(f"operational error: {result['operational_error']}")
        else:
            for field_name, value in result["facts"].items():
                print(f"{field_name}: {_print_value(value)}")
            print("identity:", json.dumps(result["identity"], ensure_ascii=False))
            if result["unsupported_fragments"]:
                print("unsupported:", "; ".join(result["unsupported_fragments"]))
            if result["grounding_issues"]:
                print("issues:", json.dumps(result["grounding_issues"], ensure_ascii=False))
            print("comparison:", json.dumps(result["comparison"], ensure_ascii=False))
            print("service:", json.dumps(result["service"], ensure_ascii=False))
            if result.get("service_status_comparison") is not None:
                print("service status comparison:", result["service_status_comparison"])
        print(f"latency: {result['latency_ms']:.2f} ms")


def _safe_endpoint(raw_url: str | None) -> str | None:
    if not raw_url:
        return None
    parsed = urlsplit(raw_url)
    host = parsed.hostname or "configured"
    netloc = host + (f":{parsed.port}" if parsed.port else "")
    return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))


def _json_default(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    raise TypeError(f"Unsupported JSON value: {type(value).__name__}")


def write_json_report(path: Path, records: list[dict], summary: dict,
                      provider_info: dict, mode: str) -> None:
    document = {
        "run_metadata": {"reference_time": REFERENCE_TIME.isoformat(),
                         "provider": provider_info, "mode": mode},
        "summary": summary,
        "cases": records,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, default=_json_default) + "\n",
        encoding="utf-8",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare deterministic and LLM Industrial Gases fact extraction."
    )
    parser.add_argument("--case", choices=[case.id for case in CASES])
    parser.add_argument("--category", choices=CATEGORIES)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--deterministic-only", action="store_true",
                       help="Run the deterministic baseline without creating an LLM provider.")
    modes.add_argument("--llm-only", action="store_true", help="Run only the LLM interpreter.")
    parser.add_argument("--provider", choices=("lmstudio", "openai"), default=None,
                        help="Provider override; default is existing LLM_PROVIDER configuration.")
    parser.add_argument("--model", default=None,
                        help="Model override; default is the existing provider configuration.")
    parser.add_argument("--json-output", type=Path,
                        help="Write machine-readable results to this path (the only file output).")
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        selected = filter_cases(CASES, args.case, args.category)
    except ValueError as error:
        parser.error(str(error))
    if not selected:
        parser.error("No evaluation cases matched the requested filter.")

    runtime = LLMRuntimeConfig.from_environment()
    from llm_client import get_default_model_name, get_default_provider_name, get_llm_provider

    provider_name = args.provider or get_default_provider_name()
    model_name = args.model or get_default_model_name(provider_name)
    endpoint = _safe_endpoint(os.getenv("LMSTUDIO_BASE_URL")) if provider_name == "lmstudio" else None
    print("INDUSTRIAL GASES MANUAL EVALUATION")
    print(f"provider: {provider_name}")
    print(f"model: {model_name or '(not configured)'}")
    print(f"timeout_seconds: {runtime.timeout_seconds}")
    print(f"max_tokens: {runtime.max_tokens}; max_output_tokens: {runtime.max_output_tokens}")
    if provider_name == "lmstudio":
        print(f"LM Studio endpoint: {endpoint or '(not configured)'}")
    print(f"cases: {len(selected)}; reference_time: {REFERENCE_TIME.isoformat()}")

    provider = None
    provider_info = {"name": provider_name, "model": model_name or None,
                     "timeout_seconds": runtime.timeout_seconds,
                     "max_tokens": runtime.max_tokens,
                     "max_output_tokens": runtime.max_output_tokens,
                     "endpoint": endpoint}
    if not args.deterministic_only:
        try:
            provider = get_llm_provider(provider_name, args.model, runtime_config=runtime)
            provider_info["model"] = getattr(provider, "model", model_name) or None
        except Exception as error:
            print(f"Provider configuration error: {type(error).__name__}: {error}")
            return 2

    records = evaluate_cases(selected, args.deterministic_only, args.llm_only, provider)
    for record in records:
        print_case(record)

    names = ("llm",) if args.llm_only else (("deterministic",) if args.deterministic_only
                                              else ("deterministic", "llm"))
    summary = summarize(records, names)
    print("\nSUMMARY")
    print("Interpreter | Cases | Matched | Missing | Unexpected | Mismatched | Grounding issues | Operational errors | Avg ms")
    for name, values in summary.items():
        print(f"{name} | {values['cases']} | {values['matched']} | {values['missing']} | "
              f"{values['unexpected']} | {values['mismatched']} | {values['grounding_issues']} | "
              f"{values['operational_errors']} | {values['average_latency_ms']:.2f}")

    if args.json_output:
        mode = "deterministic_only" if args.deterministic_only else (
            "llm_only" if args.llm_only else "comparison"
        )
        write_json_report(args.json_output, records, summary, provider_info, mode)
        print(f"JSON results: {args.json_output}")
    return 1 if any(result.get("operational_error") for case in records
                     for name, result in case.items()
                     if name in ("deterministic", "llm")) else 0


if __name__ == "__main__":
    raise SystemExit(main())
