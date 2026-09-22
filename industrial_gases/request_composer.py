"""Compose structured interpretation output into a Phase 1A request."""
from dataclasses import dataclass

from .interpretation_models import ExtractedSupplyFacts, ExtractionProvenance, ResolvedSupplyIdentity
from .models import (
    Application, ConsumptionForecast, Customer, DeliveryPlan, GasProduct,
    InventorySnapshot, Site, SupplyInstallation,
)
from .service import SupplyAssuranceRequest


@dataclass(frozen=True)
class SupplyAssuranceCompositionResult:
    request: SupplyAssuranceRequest | None = None
    missing_facts: tuple[str, ...] = ()
    missing_identities: tuple[str, ...] = ()
    unresolved_identities: tuple[str, ...] = ()
    ambiguous_identities: tuple[str, ...] = ()
    extraction_provenance: tuple[ExtractionProvenance, ...] = ()


class SupplyAssuranceRequestComposer:
    """Map already structured facts and resolved identities; never calculate."""

    def compose(self, facts: ExtractedSupplyFacts,
                identity: ResolvedSupplyIdentity) -> SupplyAssuranceCompositionResult:
        references = {
            "customer": identity.customer,
            "site": identity.site,
            "application": identity.application,
            "gas_product": identity.gas_product,
            "installation": identity.installation,
        }
        missing_ids = tuple(name for name, ref in references.items() if ref.status == "absent")
        unresolved = tuple(name for name, ref in references.items() if ref.status == "unresolved")
        ambiguous = tuple(name for name, ref in references.items() if ref.status == "ambiguous")
        if missing_ids or unresolved or ambiguous:
            return SupplyAssuranceCompositionResult(
                missing_identities=missing_ids,
                unresolved_identities=unresolved,
                ambiguous_identities=ambiguous,
                extraction_provenance=facts.provenance,
            )

        customer = identity.customer.value
        site = identity.site.value
        application = identity.application.value
        product = identity.gas_product.value
        installation = identity.installation.value
        if not isinstance(customer, Customer) or not all(
            isinstance(item, (Site, Application, GasProduct, SupplyInstallation))
            for item in (site, application, product, installation)
        ):
            return SupplyAssuranceCompositionResult(unresolved_identities=("identity",))

        snapshot = (
            InventorySnapshot(installation.installation_id, facts.reference_time, facts.current_inventory)
            if facts.current_inventory is not None and facts.reference_time is not None else None
        )
        forecast = (
            ConsumptionForecast(installation.installation_id, facts.reference_time, None, facts.consumption_rate)
            if facts.consumption_rate is not None and facts.reference_time is not None else None
        )
        delivery = (
            DeliveryPlan(installation.installation_id, facts.planned_delivery_at, facts.planned_delivery_quantity)
            if facts.planned_delivery_at is not None and facts.planned_delivery_quantity is not None else None
        )
        request = SupplyAssuranceRequest(
            customer_id=customer.customer_id,
            site=site,
            application=application,
            gas_product=product,
            installation=installation,
            inventory_snapshot=snapshot,
            consumption_forecast=forecast,
            delivery_plan=delivery,
            safety_stock=facts.safety_stock,
            reference_time=facts.reference_time,
        )
        missing_facts = tuple(name for name, value in (
            ("current_inventory", facts.current_inventory),
            ("consumption_rate", facts.consumption_rate),
            ("planned_delivery_at", facts.planned_delivery_at),
            ("planned_delivery_quantity", facts.planned_delivery_quantity),
            ("safety_stock", facts.safety_stock),
            ("reference_time", facts.reference_time),
        ) if value is None)
        return SupplyAssuranceCompositionResult(
            request=request,
            missing_facts=missing_facts,
            extraction_provenance=facts.provenance,
        )
