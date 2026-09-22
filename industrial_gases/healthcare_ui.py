"""Healthcare-specific Streamlit screen for deterministic supply assurance."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import streamlit as st

from .models import (
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
    SupplyInstallation,
)
from .service import SupplyAssuranceRequest, SupplyAssuranceResult, SupplyAssuranceService


_REFERENCE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)
_CUSTOMER_ID = "hospital-costa-sur"
_SITE_ID = "hospital-costa-sur-site"
_APPLICATION_ID = "hospital-costa-sur-medical-oxygen"
_PRODUCT_ID = "medical-oxygen"
_INSTALLATION_ID = "hospital-costa-sur-bulk-cryogenic-o2"

_MISSING_INPUT_LABELS = {
    "customer_id": "Customer",
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
_FINDING_LABELS = {
    "safety_stock_breach": "Projected inventory before delivery is below the configured safety stock.",
    "stockout_before_delivery": "Projected physical inventory reaches zero before the planned delivery.",
    "capacity_overflow": "Projected post-delivery inventory exceeds installation capacity.",
}


def _build_request(
    *,
    customer_label: str = "Hospital Costa Sur",
    site_label: str = "Hospital Costa Sur",
    capacity_kg: int | float = 10000,
    current_inventory_kg: int | float = 3200,
    consumption_kg_per_day: int | float = 700,
    days_until_delivery: int = 4,
    planned_delivery_kg: int | float = 4000,
    safety_stock_kg: int | float = 1500,
) -> SupplyAssuranceRequest:
    """Build the explicit single-installation demo request from form values."""
    reference_time = _REFERENCE_TIME
    delivery_at = reference_time + timedelta(days=days_until_delivery)

    customer = Customer(_CUSTOMER_ID, customer_label)
    site = Site(_SITE_ID, customer.customer_id, site_label)
    application = Application(
        _APPLICATION_ID,
        site.site_id,
        "Medicinal oxygen",
        (ApplicationGasRequirement(_PRODUCT_ID, "medical_supply"),),
    )
    gas_product = GasProduct(_PRODUCT_ID, "O2 medicinal / medical oxygen", "liquid")
    installation = SupplyInstallation(
        _INSTALLATION_ID,
        site.site_id,
        gas_product.gas_product_id,
        "bulk",
        "cryogenic_tank",
        Quantity(capacity_kg, "kg"),
    )
    snapshot = InventorySnapshot(
        installation.installation_id,
        reference_time,
        Quantity(current_inventory_kg, "kg"),
    )
    forecast = ConsumptionForecast(
        installation.installation_id,
        reference_time,
        delivery_at,
        ConsumptionRate(consumption_kg_per_day, "kg", "day"),
    )
    delivery = DeliveryPlan(
        installation.installation_id,
        delivery_at,
        Quantity(planned_delivery_kg, "kg"),
    )
    return SupplyAssuranceRequest(
        customer_id=customer.customer_id,
        site=site,
        application=application,
        gas_product=gas_product,
        installation=installation,
        inventory_snapshot=snapshot,
        consumption_forecast=forecast,
        delivery_plan=delivery,
        safety_stock=Quantity(safety_stock_kg, "kg"),
        reference_time=reference_time,
    )


def _format_number(value: Decimal | int | float) -> str:
    number = value if isinstance(value, Decimal) else Decimal(str(value))
    return format(number.normalize(), "f")


def _quantity_text(quantity: Quantity) -> str:
    return f"{_format_number(quantity.value)} {quantity.unit}"


def _render_traceability(result: SupplyAssuranceResult) -> None:
    if result.projection is None:
        return
    with st.expander("Traceability"):
        rows = []
        for item in result.projection.provenance:
            value = item.value
            if hasattr(value, "unit") and hasattr(value, "value"):
                rendered = _quantity_text(value)
            elif isinstance(value, Decimal):
                rendered = _format_number(value)
            else:
                rendered = str(value)
            rows.append({
                "Field": item.field,
                "Value": rendered,
                "Origin": item.origin,
                "Source": item.source,
            })
        st.dataframe(rows, hide_index=True, use_container_width=True)


def render_supply_assurance_result(result: SupplyAssuranceResult) -> None:
    """Present service status and structured outputs without recalculating them."""
    st.markdown("### CALCULATED RESULTS")
    st.caption(f"Status: {result.status}")

    if result.status == "MISSING_INPUTS":
        st.write("Missing inputs:")
        for name in result.missing_inputs:
            st.write(f"- {_MISSING_INPUT_LABELS.get(name, name)}")
        return
    if result.status == "INVALID":
        st.write("Validation errors:")
        for error in result.validation_errors:
            st.write(f"- {error}")
        return
    if result.status != "COMPLETED" or result.projection is None:
        st.error("Supply assurance returned an inconsistent result.")
        return

    projection = result.projection
    days = "—" if projection.days_of_supply is None else f"{projection.days_of_supply:.6f} days"
    values = (
        ("Days of supply", days),
        ("Consumption until delivery", _quantity_text(projection.consumption_until_delivery)),
        ("Inventory before delivery", _quantity_text(projection.inventory_immediately_before_delivery)),
        ("Configured safety stock", _quantity_text(projection.safety_stock)),
        ("Gap to safety stock", _quantity_text(projection.safety_stock_gap_before_delivery)),
        ("Planned delivery", _quantity_text(projection.planned_delivery_quantity)),
        ("Inventory after delivery", _quantity_text(projection.inventory_immediately_after_delivery)),
        ("Stockout before delivery", "Yes" if projection.stockout_before_delivery else "No"),
        ("Minimum quantity needed at delivery", _quantity_text(projection.required_delivery_volume)),
        ("Capacity exceeded", "Yes" if projection.capacity_exceeded else "No"),
        ("Overflow", _quantity_text(projection.capacity_overflow)),
    )
    for start in range(0, len(values), 3):
        columns = st.columns(3)
        for column, (label, value) in zip(columns, values[start:start + 3]):
            column.metric(label, value)
    st.caption(
        "Minimum quantity that would need to arrive at the planned-delivery "
        "instant to restore the configured safety stock under the current "
        "forecast. This is not an additional quantity on top of the planned delivery."
    )
    st.caption(
        "Inventory below configured safety stock is not the same as a stockout."
    )

    st.markdown("### FINDINGS")
    if result.findings:
        for finding in result.findings:
            label = _FINDING_LABELS.get(finding.code, finding.code)
            st.write(f"- {label}")
    else:
        st.write("No findings reported.")
    _render_traceability(result)


def render_healthcare_supply_assurance() -> None:
    """Render and run the deterministic O2 Healthcare supply-assurance demo."""
    st.header("Healthcare Supply Assurance")
    st.caption("Deterministic analysis — no LLM or RAG")
    st.caption(f"Reference time: {_REFERENCE_TIME.isoformat()}")

    with st.form("healthcare_supply_assurance_form"):
        st.markdown("### INPUTS")
        first, second = st.columns(2)
        customer_label = first.text_input("Customer label", "Hospital Costa Sur", key="healthcare_customer_label")
        site_label = second.text_input("Site label", "Hospital Costa Sur", key="healthcare_site_label")
        capacity = first.number_input("Installation capacity (kg)", min_value=1.0, value=10000.0, step=100.0,
                                      key="healthcare_capacity_kg")
        inventory = second.number_input("Current inventory (kg)", min_value=0.0, value=3200.0, step=100.0,
                                        key="healthcare_inventory_kg")
        consumption = first.number_input("Forecast consumption (kg/day)", min_value=0.0, value=700.0, step=10.0,
                                         key="healthcare_consumption_kg_day")
        delivery_days = second.number_input("Days until next delivery", min_value=0, value=4, step=1,
                                            key="healthcare_days_until_delivery")
        planned_delivery = first.number_input("Planned delivery quantity (kg)", min_value=0.0, value=4000.0,
                                              step=100.0, key="healthcare_planned_delivery_kg")
        safety_stock = second.number_input("Safety stock (kg)", min_value=0.0, value=1500.0, step=100.0,
                                           key="healthcare_safety_stock_kg")
        submitted = st.form_submit_button("Analyze supply assurance", type="primary")

    if submitted:
        request = _build_request(
            customer_label=customer_label,
            site_label=site_label,
            capacity_kg=capacity,
            current_inventory_kg=inventory,
            consumption_kg_per_day=consumption,
            days_until_delivery=delivery_days,
            planned_delivery_kg=planned_delivery,
            safety_stock_kg=safety_stock,
        )
        st.session_state.healthcare_supply_assurance_result = SupplyAssuranceService().assess(request)

    result = st.session_state.get("healthcare_supply_assurance_result")
    if result is not None:
        render_supply_assurance_result(result)
