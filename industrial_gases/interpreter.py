"""Small deterministic natural-language boundary for supply assurance."""
from dataclasses import dataclass
from datetime import datetime, timedelta
import re
import unicodedata

from .interpretation_models import (
    ExtractedSupplyFacts, ExtractionIssue, ExtractionProvenance, IdentityReference,
    ResolvedSupplyIdentity,
)
from .models import ConsumptionRate, Quantity
from .units import UNIT_CATALOG
from .lexical import normalize_unit_lexeme, parse_decimal_number, relative_days_evidence


@dataclass(frozen=True)
class SupplyAssuranceInterpretationResult:
    facts: ExtractedSupplyFacts
    identity: ResolvedSupplyIdentity
    unsupported_fragments: tuple[str, ...] = ()
    issues: tuple[ExtractionIssue, ...] = ()


class SupplyAssuranceIdentityContext:
    """Explicit in-memory label context; it does not invent identifiers."""

    def __init__(self, entries: dict[str, ResolvedSupplyIdentity]):
        self._entries = {label.casefold(): value for label, value in entries.items()}

    @staticmethod
    def _resolved(refs):
        values = {id(ref.value): ref.value for ref in refs if ref.value is not None}
        if len(values) == 1:
            return IdentityReference(next(iter(values.values())))
        if len(values) > 1:
            candidates = []
            for value in values.values():
                candidates.append(getattr(value, "installation_id", getattr(value, "gas_product_id", str(value))))
            return IdentityReference(candidates=tuple(sorted(candidates)))
        return IdentityReference()

    def resolve(self, text: str) -> ResolvedSupplyIdentity:
        lowered = text.casefold()
        folded = "".join(c for c in unicodedata.normalize("NFKD", lowered)
                          if not unicodedata.combining(c)).replace("�", "i")
        entries = list(self._entries.items())
        customer_matches = [identity for label, identity in entries
                            if ("hospital" in label or label == identity.customer.value.name.casefold())
                            and label in lowered]
        product_matches = [identity for label, identity in entries
                           if identity.gas_product.value is not None and
                           ("".join(c for c in unicodedata.normalize("NFKD", identity.gas_product.value.name.casefold())
                                    if not unicodedata.combining(c)).replace("�", "i") in folded or
                            ("ox" in folded and "geno" in folded and "ox" in identity.gas_product.value.name.casefold()) or
                            identity.gas_product.value.gas_product_id.casefold() in lowered)]
        installation_matches = [identity for label, identity in entries
                                if ("tanque" in label or "installation" in label or "instal" in label)
                                and (label in lowered or ("tanque" in lowered and "tanque" in label))]
        customer_label = re.search(r"\b(?:Hospital|Centro)\s+[^:,.\n]+", text, re.I)
        product_label = re.search(r"\b(?:ox(?:i|í|�)geno(?:\s+medicinal)?|nitr(?:o|ó|�)geno(?:\s+medicinal)?|CO2|N2|producto\s+[\wÁÉÍÓÚÑáéíóúñ -]+)\b", text, re.I)
        customer = self._resolved([identity.customer for identity in customer_matches])
        product = self._resolved([identity.gas_product for identity in product_matches])
        installation = self._resolved([identity.installation for identity in installation_matches])
        if installation.status == "absent" and len(customer_matches) == 1 and product.status == "resolved":
            installation = customer_matches[0].installation
        if customer.status == "absent" and customer_label:
            customer = IdentityReference(label=customer_label.group(0).strip())
        if product.status == "absent" and product_label:
            product = IdentityReference(label=product_label.group(0).strip())
        return ResolvedSupplyIdentity(
            customer=customer,
            site=self._resolved([identity.site for identity in customer_matches]),
            application=self._resolved([identity.application for identity in customer_matches]),
            gas_product=product,
            installation=installation,
        )

    def resolve_labels(self, labels: dict[str, str | None]) -> ResolvedSupplyIdentity:
        """Resolve extracted labels against known names, never against IDs."""
        def normalized(value: str) -> str:
            return "".join(char for char in unicodedata.normalize("NFKD", value.casefold())
                           if not unicodedata.combining(char))

        entries = tuple(self._entries.items())

        def supplied(field: str) -> str | None:
            value = labels.get(field)
            return value.strip() if isinstance(value, str) and value.strip() else None

        def match_name(field: str, label: str | None):
            if label is None:
                return []
            refs = []
            for _alias, identity in entries:
                reference = getattr(identity, field)
                value = reference.value
                name = getattr(value, "name", None)
                if isinstance(name, str) and normalized(name) == normalized(label):
                    refs.append(reference)
            return refs

        customer_label = supplied("customer_label")
        site_label = supplied("site_label")
        application_label = supplied("application_label")
        product_label = supplied("gas_product_label")
        installation_label = supplied("installation_label")

        customer = self._resolved(match_name("customer", customer_label))
        if customer_label and customer.status == "absent":
            customer = IdentityReference(label=customer_label)
        product = self._resolved(match_name("gas_product", product_label))
        if product_label and product.status == "absent":
            product = IdentityReference(label=product_label)

        if site_label:
            site = self._resolved(match_name("site", site_label))
            if site.status == "absent":
                site = IdentityReference(label=site_label)
        elif customer.value is not None:
            site = self._resolved([
                identity.site for _alias, identity in entries
                if identity.customer.value is not None
                and identity.customer.value.customer_id == customer.value.customer_id
            ])
        else:
            site = IdentityReference()

        if application_label:
            application = self._resolved(match_name("application", application_label))
            if application.status == "absent":
                application = IdentityReference(label=application_label)
        elif site.value is not None:
            application = self._resolved([
                identity.application for _alias, identity in entries
                if identity.application.value is not None
                and identity.application.value.site_id == site.value.site_id
            ])
        else:
            application = IdentityReference()

        if installation_label:
            installation_refs = []
            target = normalized(installation_label)
            for alias, identity in entries:
                value = identity.installation.value
                if value is None:
                    continue
                alias_key = normalized(alias)
                # A general label may identify several known installations; keep
                # that state ambiguous instead of selecting one.
                if alias_key == target or ("tanque" in target and alias_key.startswith(target + " ")):
                    installation_refs.append(identity.installation)
            installation = self._resolved(installation_refs)
            if installation.status == "absent":
                installation = IdentityReference(label=installation_label)
        elif site.value is not None and product.value is not None:
            installation = self._resolved([
                identity.installation for _alias, identity in entries
                if identity.installation.value is not None
                and identity.installation.value.site_id == site.value.site_id
                and identity.installation.value.gas_product_id == product.value.gas_product_id
            ])
        else:
            installation = IdentityReference()
        return ResolvedSupplyIdentity(
            customer=customer,
            site=site,
            application=application,
            gas_product=product,
            installation=installation,
        )


class SupplyAssuranceInterpreter:
    """Extract only explicitly supported facts; never calculate assurance."""

    _units = "(?:kg|t|Nm3|Sm3|m3|L)"
    _number = r"\d[\d .]*(?:,\d+)?"

    def __init__(self, identity_context: SupplyAssuranceIdentityContext):
        self.identity_context = identity_context

    @staticmethod
    def _number_value(raw: str) -> float:
        return float(parse_decimal_number(raw))

    @staticmethod
    def _unit(raw: str) -> str:
        normalized = normalize_unit_lexeme(raw)
        return {"m\ufffd": "m3", "Nm\ufffd": "Nm3", "Sm\ufffd": "Sm3",
                "nm\ufffd": "Nm3", "sm\ufffd": "Sm3"}.get(normalized, normalized)

    def interpret(self, text: str, reference_time: datetime) -> SupplyAssuranceInterpretationResult:
        if reference_time.tzinfo is None or reference_time.utcoffset() is None:
            raise ValueError("reference_time must be timezone-aware")
        provenance = []
        values = {}
        unsupported = []

        def quantity_match(pattern: str, field: str):
            match = re.search(pattern, text, re.I)
            if not match:
                return
            raw, unit = match.group("value"), self._unit(match.group("unit"))
            if unit not in UNIT_CATALOG:
                unsupported.append(match.group(0))
                return
            values[field] = Quantity(self._number_value(raw), unit)
            provenance.append(ExtractionProvenance(field, "explicit_input", "deterministic", match.group(0)))

        superscript_three = "\u00b3"
        unit_pattern = (r"(?:kg|t|Nm3" + superscript_three + r"|Sm3" + superscript_three +
                        r"|m3" + superscript_three + r"|Nm3|Sm3|m3|L|m" + superscript_three +
                        r"|Nm" + superscript_three + r"|Sm" + superscript_three +
                        r"|m\ufffd|Nm\ufffd|Sm\ufffd)(?=\s|[.,;]|$)")
        quantity_match(rf"(?:tenemos|stock\s+actual(?:\s+de)?|inventario(?:\s+de)?)[^\.\n]*?[:=]?\s*(?P<value>\d[\d .]*(?:,\d+)?)\s*(?P<unit>{unit_pattern})", "current_inventory")
        quantity_match(rf"(?:entrega(?:\s+prevista)?|recibiremos|entrega\s+de)[^\.\n]*?[:=]?\s*(?P<value>\d[\d .]*(?:,\d+)?)\s*(?P<unit>{unit_pattern})", "planned_delivery_quantity")
        quantity_match(rf"(?:stock\s+de\s+seguridad|safety\s+stock)[^\.\n]*?[:=]?\s*(?P<value>\d[\d .]*(?:,\d+)?)\s*(?P<unit>{unit_pattern})", "safety_stock")
        unsupported_unit = re.search(
            r"(?:tenemos|stock\s+actual|inventario)\b[^\.\n:]*?\d[\d .]*(?:,\d+)?\s+(?P<unit>[A-Za-zÁÉÍÓÚÑáéíóúñ%]+)",
            text, re.I)
        if unsupported_unit and unsupported_unit.group("unit") not in UNIT_CATALOG:
            unsupported.append(unsupported_unit.group(0))

        consumption = re.search(rf"(?:consumimos|consumo\s+(?:diario|previsto))[^\.\n]*?[:=]?\s*(?P<value>\d[\d .]*(?:,\d+)?)\s*(?P<unit>{unit_pattern})\s*(?:/\s*d[ií]a|al\s+d[ií]a)?", text, re.I)
        if consumption:
            unit = self._unit(consumption.group("unit"))
            if unit in UNIT_CATALOG:
                values["consumption_rate"] = ConsumptionRate(self._number_value(consumption.group("value")), unit, "day")
                provenance.append(ExtractionProvenance("consumption_rate", "explicit_input", "deterministic", consumption.group(0)))

        days = re.search(r"dentro\s+de\s+\d+\s+d[ií]as", text, re.I)
        if days:
            day_count = relative_days_evidence(days.group(0))
            values["planned_delivery_at"] = reference_time + timedelta(days=day_count)
            provenance.append(ExtractionProvenance("planned_delivery_at", "explicit_input", "deterministic", days.group(0)))
        for unsupported_expression in (r"\bma[ñn]ana\b", r"pr[oó]ximo\s+jueves", r"final\s+de\s+semana"):
            match = re.search(unsupported_expression, text, re.I)
            if match:
                unsupported.append(match.group(0))
        level = re.search(r"\b(?:tanque|nivel)[^\.\n]*\d[\d .]*(?:,\d+)?\s*%", text, re.I)
        if level:
            unsupported.append(level.group(0))

        facts = ExtractedSupplyFacts(
            current_inventory=values.get("current_inventory"),
            consumption_rate=values.get("consumption_rate"),
            planned_delivery_at=values.get("planned_delivery_at"),
            planned_delivery_quantity=values.get("planned_delivery_quantity"),
            safety_stock=values.get("safety_stock"),
            reference_time=reference_time,
            provenance=tuple(provenance),
        )
        return SupplyAssuranceInterpretationResult(facts, self.identity_context.resolve(text), tuple(unsupported))
