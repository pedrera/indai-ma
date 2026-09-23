"""Read-only Streamlit view over independently evaluated supply positions."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import streamlit as st

from .models import (
    Application,
    ApplicationGasRequirement,
    ConsumptionForecast,
    ConsumptionRate,
    DeliveryPlan,
    GasProduct,
    InventorySnapshot,
    Quantity,
    Site,
    SupplyInstallation,
)
from .portfolio import (
    SupplyPortfolioItem,
    SupplyPortfolioRequest,
    SupplyPortfolioService,
)
from .service import SupplyAssuranceRequest, SupplyAssuranceResult


_REFERENCE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)
_CUSTOMER_LABELS = {
    "hospital-costa-sur": "Hospital Costa Sur",
    "alimentos-del-sur": "Alimentos del Sur",
}
_FINDING_LABELS = {
    "safety_stock_breach": "Projected inventory before delivery is below configured safety stock.",
    "stockout_before_delivery": "Physical inventory reaches zero before the planned delivery.",
    "capacity_overflow": "Projected post-delivery inventory exceeds installation capacity.",
}
_MISSING_INPUT_LABELS = {
    "site": "Site",
    "application": "Application",
    "gas_product": "Gas product",
    "installation": "Installation",
    "inventory_snapshot": "Current inventory",
    "consumption_forecast": "Consumption forecast",
    "delivery_plan": "Planned delivery",
    "safety_stock": "Safety stock",
    "reference_time": "Reference time",
}


def _request(
    *,
    customer_id: str,
    site_id: str,
    site_name: str,
    application_id: str,
    application_name: str,
    gas_product_id: str,
    gas_product_name: str,
    role: str,
    installation_id: str,
    unit: str,
    capacity: int,
    inventory: int,
    consumption_per_day: int,
    planned_delivery: int,
    safety_stock: int,
) -> SupplyAssuranceRequest:
    site = Site(site_id, customer_id, site_name)
    application = Application(
        application_id,
        site_id,
        application_name,
        (ApplicationGasRequirement(gas_product_id, role),),
    )
    product = GasProduct(gas_product_id, gas_product_name, "liquid" if gas_product_id == "medical-oxygen" else "gas")
    installation = SupplyInstallation(
        installation_id,
        site_id,
        gas_product_id,
        "bulk",
        "cryogenic_tank" if gas_product_id == "medical-oxygen" else "storage_installation",
        Quantity(capacity, unit),
    )
    delivery_at = _REFERENCE_TIME + timedelta(days=4)
    return SupplyAssuranceRequest(
        customer_id=customer_id,
        site=site,
        application=application,
        gas_product=product,
        installation=installation,
        inventory_snapshot=InventorySnapshot(
            installation_id, _REFERENCE_TIME, Quantity(inventory, unit),
        ),
        consumption_forecast=ConsumptionForecast(
            installation_id, _REFERENCE_TIME, delivery_at,
            ConsumptionRate(consumption_per_day, unit, "day"),
        ),
        delivery_plan=DeliveryPlan(
            installation_id, delivery_at, Quantity(planned_delivery, unit),
        ),
        safety_stock=Quantity(safety_stock, unit),
        reference_time=_REFERENCE_TIME,
    )


def _canonical_portfolio_request() -> SupplyPortfolioRequest:
    """Build fixed, explicit demo inputs with no dependency on the current clock."""
    items = (
        SupplyPortfolioItem(
            "hospital-costa-sur-o2",
            _request(
                customer_id="hospital-costa-sur",
                site_id="hospital-costa-sur-site",
                site_name="Hospital Costa Sur",
                application_id="hospital-costa-sur-medical-oxygen",
                application_name="Medicinal oxygen",
                gas_product_id="medical-oxygen",
                gas_product_name="O2 / Medicinal oxygen",
                role="medical_supply",
                installation_id="hospital-costa-sur-bulk-cryogenic-o2",
                unit="kg",
                capacity=10000,
                inventory=3200,
                consumption_per_day=700,
                planned_delivery=4000,
                safety_stock=1500,
            ),
        ),
        SupplyPortfolioItem(
            "alimentos-sur-malaga-co2",
            _request(
                customer_id="alimentos-del-sur",
                site_id="malaga-production-plant",
                site_name="Málaga Production Plant",
                application_id="beverage-carbonation",
                application_name="Beverage carbonation",
                gas_product_id="co2",
                gas_product_name="CO2",
                role="carbonation",
                installation_id="co2-bulk-installation",
                unit="kg",
                capacity=1000,
                inventory=300,
                consumption_per_day=50,
                planned_delivery=250,
                safety_stock=150,
            ),
        ),
        SupplyPortfolioItem(
            "alimentos-sur-malaga-n2",
            _request(
                customer_id="alimentos-del-sur",
                site_id="malaga-production-plant",
                site_name="Málaga Production Plant",
                application_id="modified-atmosphere-inerting",
                application_name="Modified atmosphere / inerting",
                gas_product_id="n2",
                gas_product_name="N2",
                role="inerting",
                installation_id="n2-bulk-installation",
                unit="Nm3",
                capacity=2000,
                inventory=900,
                consumption_per_day=100,
                planned_delivery=300,
                safety_stock=400,
            ),
        ),
    )
    return SupplyPortfolioRequest(items)


def _format_number(value: Decimal | int | float) -> str:
    number = value if isinstance(value, Decimal) else Decimal(str(value))
    return format(number.normalize(), "f")


def _quantity_text(quantity: Quantity) -> str:
    return f"{_format_number(quantity.value)} {quantity.unit}"


def _render_item(item) -> None:
    request = item.request
    result: SupplyAssuranceResult = item.result
    customer_id = result.customer_id or request.customer_id or "—"
    customer_label = _CUSTOMER_LABELS.get(customer_id, customer_id)
    site = request.site
    application = request.application
    product = request.gas_product
    installation = request.installation

    st.markdown(f"#### {product.name if product is not None else 'Gas position'}")
    st.caption(f"Item ID: {item.item_id}")
    st.write(f"Customer: {customer_label} ({customer_id})")
    st.write(f"Site: {site.name if site is not None else '—'}")
    st.write(f"Application: {application.name if application is not None else '—'}")
    st.write(f"Gas product: {product.name if product is not None else '—'}")
    st.write(f"Installation: {installation.installation_id if installation is not None else '—'}")
    unit = installation.capacity.unit if installation is not None else "—"
    st.write(f"Operational unit: {unit}")
    st.caption(f"Status: {result.status}")

    if result.status == "MISSING_INPUTS":
        st.write("Missing inputs:")
        for field in result.missing_inputs:
            st.write(f"- {_MISSING_INPUT_LABELS.get(field, field)}")
        return
    if result.status == "INVALID":
        st.write("Validation errors:")
        for error in result.validation_errors:
            st.write(f"- {error}")
        return
    if result.status != "COMPLETED" or result.projection is None:
        st.error("This item returned an inconsistent supply assurance result.")
        return

    projection = result.projection
    days = "—" if projection.days_of_supply is None else f"{projection.days_of_supply:.6f} days"
    metrics = (
        ("Current inventory", _quantity_text(projection.current_inventory)),
        ("Days of supply", days),
        ("Inventory before delivery", _quantity_text(projection.inventory_immediately_before_delivery)),
        ("Configured safety stock", _quantity_text(projection.safety_stock)),
        ("Safety stock gap", _quantity_text(projection.safety_stock_gap_before_delivery)),
        ("Stockout before delivery", "Yes" if projection.stockout_before_delivery else "No"),
        ("Planned delivery", _quantity_text(projection.planned_delivery_quantity)),
        ("Inventory after delivery", _quantity_text(projection.inventory_immediately_after_delivery)),
    )
    st.markdown("**Calculated results**")
    for offset in range(0, len(metrics), 2):
        columns = st.columns(2)
        for column, (label, value) in zip(columns, metrics[offset:offset + 2]):
            column.metric(label, value)

    st.markdown("**Findings**")
    if result.findings:
        for finding in result.findings:
            st.write(f"- {_FINDING_LABELS.get(finding.code, finding.code)}")
    else:
        st.write("No findings reported.")


def render_supply_portfolio() -> None:
    """Render three independent demo positions through the portfolio service."""
    st.header("Supply Portfolio")
    st.caption(
        "Each supply position is evaluated independently using its own application, "
        "gas product, installation and operational unit."
    )
    st.caption(
        "Portfolio groups independent results for operational visibility; "
        "quantities are not aggregated across positions."
    )
    st.caption("Deterministic demo view with structured example inputs, not industry standards.")
    request = _canonical_portfolio_request()
    result = SupplyPortfolioService().evaluate(request)
    st.write(f"{len(result.items)} supply positions")

    previous_group = None
    for item in result.items:
        current_group = (
            item.request.customer_id,
            item.request.site.site_id if item.request.site is not None else None,
        )
        if current_group != previous_group:
            customer_id = item.result.customer_id or item.request.customer_id or "—"
            customer_label = _CUSTOMER_LABELS.get(customer_id, customer_id)
            st.markdown(f"### {customer_label}")
            if item.request.site is not None:
                st.markdown(f"#### {item.request.site.name}")
            previous_group = current_group
        _render_item(item)
