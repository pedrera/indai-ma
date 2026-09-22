"""Healthcare-specific Streamlit screen for deterministic supply assurance."""
from dataclasses import replace
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
from .supply_scenarios import (
    SupplyAssuranceAlternative,
    SupplyAssuranceScenarioResult,
    evaluate_supply_assurance_alternative,
)


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
_WHAT_IF_TYPES = (
    "Earlier delivery",
    "Different planned delivery quantity",
    "Hypothetical consumption forecast",
)
_WHAT_IF_IDS = {
    "Earlier delivery": "healthcare-earlier-delivery",
    "Different planned delivery quantity": "healthcare-delivery-quantity",
    "Hypothetical consumption forecast": "healthcare-revised-consumption-forecast",
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


def _days_text(value: Decimal | None) -> str:
    return "—" if value is None else f"{value:.6f} days"


def _render_findings(findings) -> None:
    if findings:
        for finding in findings:
            label = _FINDING_LABELS.get(finding.code, finding.code)
            st.write(f"- {label}")
    else:
        st.write("No findings reported.")


def _build_supply_assurance_alternative(
    baseline_request: SupplyAssuranceRequest,
    what_if_type: str,
    explicit_value: int | float,
) -> SupplyAssuranceAlternative:
    """Construct one supported request explicitly without generic input patching."""
    if what_if_type == "Earlier delivery":
        delivery_at = baseline_request.reference_time + timedelta(days=int(explicit_value))
        alternative_request = replace(
            baseline_request,
            delivery_plan=replace(baseline_request.delivery_plan, planned_delivery_at=delivery_at),
        )
    elif what_if_type == "Different planned delivery quantity":
        alternative_request = replace(
            baseline_request,
            delivery_plan=replace(
                baseline_request.delivery_plan,
                planned_quantity=Quantity(explicit_value, "kg"),
            ),
        )
    elif what_if_type == "Hypothetical consumption forecast":
        alternative_request = replace(
            baseline_request,
            consumption_forecast=replace(
                baseline_request.consumption_forecast,
                rate=ConsumptionRate(explicit_value, "kg", "day"),
            ),
        )
    else:
        raise ValueError(f"Unsupported Healthcare what-if type: {what_if_type}")
    return SupplyAssuranceAlternative(
        id=_WHAT_IF_IDS[what_if_type],
        label=what_if_type,
        alternative_request=alternative_request,
    )


def _what_changed_text(
    baseline_request: SupplyAssuranceRequest,
    alternative: SupplyAssuranceAlternative,
) -> str:
    changed = alternative.alternative_request
    if alternative.id == _WHAT_IF_IDS["Earlier delivery"]:
        if (baseline_request.delivery_plan is None or changed.delivery_plan is None or
                baseline_request.reference_time is None or changed.reference_time is None):
            return alternative.label
        baseline_days = (baseline_request.delivery_plan.planned_delivery_at - baseline_request.reference_time).days
        alternative_days = (changed.delivery_plan.planned_delivery_at - changed.reference_time).days
        return f"Delivery timing — Baseline: {baseline_days} days; Alternative: {alternative_days} days"
    if alternative.id == _WHAT_IF_IDS["Different planned delivery quantity"]:
        if (baseline_request.delivery_plan is None or changed.delivery_plan is None):
            return alternative.label
        return (
            "Planned delivery quantity — Baseline: "
            f"{_quantity_text(baseline_request.delivery_plan.planned_quantity)}; Alternative: "
            f"{_quantity_text(changed.delivery_plan.planned_quantity)}"
        )
    if (baseline_request.consumption_forecast is None or changed.consumption_forecast is None):
        return alternative.label
    return (
        "Hypothetical consumption forecast — Baseline: "
        f"{_format_number(baseline_request.consumption_forecast.rate.value)} "
        f"{baseline_request.consumption_forecast.rate.quantity_unit}/day; Alternative: "
        f"{_format_number(changed.consumption_forecast.rate.value)} "
        f"{changed.consumption_forecast.rate.quantity_unit}/day"
    )


def _render_scenario_status(title: str, result: SupplyAssuranceResult) -> None:
    st.markdown(f"#### {title}")
    st.caption(f"Status: {result.status}")
    if result.status == "MISSING_INPUTS":
        st.write("Missing inputs:")
        for name in result.missing_inputs:
            st.write(f"- {_MISSING_INPUT_LABELS.get(name, name)}")
    elif result.status == "INVALID":
        st.write("Validation errors:")
        for error in result.validation_errors:
            st.write(f"- {error}")


def _scenario_metrics(what_if_type: str, projection) -> tuple[tuple[str, str], ...]:
    if what_if_type == "Different planned delivery quantity":
        return (
            ("Inventory after delivery", _quantity_text(projection.inventory_immediately_after_delivery)),
            ("Capacity exceeded", "Yes" if projection.capacity_exceeded else "No"),
            ("Overflow", _quantity_text(projection.capacity_overflow)),
        )
    return (
        ("Days of supply", _days_text(projection.days_of_supply)),
        ("Consumption until delivery", _quantity_text(projection.consumption_until_delivery)),
        ("Inventory before delivery", _quantity_text(projection.inventory_immediately_before_delivery)),
        ("Gap to safety stock", _quantity_text(projection.safety_stock_gap_before_delivery)),
        ("Stockout before delivery", "Yes" if projection.stockout_before_delivery else "No"),
        ("Inventory after delivery", _quantity_text(projection.inventory_immediately_after_delivery)),
        ("Minimum quantity needed at delivery", _quantity_text(projection.required_delivery_volume)),
    )


def _render_supply_assurance_scenario(result: SupplyAssuranceScenarioResult) -> None:
    st.markdown("### WHAT CHANGED")
    baseline_request = st.session_state.get("healthcare_supply_assurance_request")
    if baseline_request is not None:
        st.write(_what_changed_text(baseline_request, result.alternative))
    else:
        st.write(result.alternative.label)

    st.markdown("### BASELINE vs ALTERNATIVE")
    baseline_result = result.baseline_result
    alternative_result = result.alternative_result
    if (baseline_result.status != "COMPLETED" or baseline_result.projection is None or
            alternative_result.status != "COMPLETED" or alternative_result.projection is None):
        baseline_column, alternative_column = st.columns(2)
        with baseline_column:
            _render_scenario_status("BASELINE", baseline_result)
        with alternative_column:
            _render_scenario_status("ALTERNATIVE", alternative_result)
        return

    what_if_type = next(
        (name for name, alternative_id in _WHAT_IF_IDS.items()
         if alternative_id == result.alternative.id),
        None,
    )
    if what_if_type is None:
        st.error("The stored Healthcare scenario type is not recognized.")
        return

    baseline_column, alternative_column = st.columns(2)
    with baseline_column:
        st.markdown("#### BASELINE")
        for label, value in _scenario_metrics(what_if_type, baseline_result.projection):
            st.metric(label, value)
    with alternative_column:
        st.markdown("#### ALTERNATIVE")
        for label, value in _scenario_metrics(what_if_type, alternative_result.projection):
            st.metric(label, value)

    st.markdown("#### Baseline findings")
    _render_findings(baseline_result.findings)
    st.markdown("#### Alternative findings")
    _render_findings(alternative_result.findings)


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
    _render_findings(result.findings)
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
        st.session_state.healthcare_supply_assurance_request = request
        st.session_state.healthcare_supply_assurance_result = SupplyAssuranceService().assess(request)
        st.session_state.healthcare_supply_scenario_result = None

    result = st.session_state.get("healthcare_supply_assurance_result")
    if result is not None:
        render_supply_assurance_result(result)

    st.markdown("### WHAT-IF ANALYSIS")
    st.caption(
        "What-if analysis compares deterministic consequences of an explicit "
        "assumption using the same rules. It does not recommend or rank alternatives."
    )
    baseline_request = st.session_state.get("healthcare_supply_assurance_request")
    if baseline_request is None:
        st.info("Run the baseline supply assurance first to enable a what-if comparison.")
    else:
        what_if_type = st.selectbox(
            "Scenario type",
            _WHAT_IF_TYPES,
            key="healthcare_what_if_type",
        )
        baseline_days = (
            baseline_request.delivery_plan.planned_delivery_at - baseline_request.reference_time
        ).days
        if what_if_type == "Earlier delivery" and baseline_days <= 0:
            st.info("The current baseline delivery is already at the reference time; no earlier time can be selected.")
        else:
            with st.form("healthcare_what_if_form"):
                if what_if_type == "Earlier delivery":
                    explicit_value = st.number_input(
                        "Days until alternative delivery",
                        min_value=0,
                        max_value=baseline_days - 1,
                        value=min(3, baseline_days - 1),
                        step=1,
                        key="healthcare_what_if_delivery_days",
                    )
                elif what_if_type == "Different planned delivery quantity":
                    explicit_value = st.number_input(
                        "Alternative planned delivery quantity (kg)",
                        min_value=0.0,
                        value=5000.0,
                        step=100.0,
                        key="healthcare_what_if_delivery_quantity_kg",
                    )
                else:
                    explicit_value = st.number_input(
                        "Hypothetical consumption forecast (kg/day)",
                        min_value=0.0,
                        value=500.0,
                        step=10.0,
                        key="healthcare_what_if_consumption_rate_kg_day",
                    )
                submitted_what_if = st.form_submit_button("Analyze baseline vs alternative", type="primary")
            if submitted_what_if:
                alternative = _build_supply_assurance_alternative(
                    baseline_request, what_if_type, explicit_value,
                )
                st.session_state.healthcare_supply_scenario_result = evaluate_supply_assurance_alternative(
                    baseline_request, alternative, SupplyAssuranceService(),
                )

    scenario_result = st.session_state.get("healthcare_supply_scenario_result")
    if scenario_result is not None:
        _render_supply_assurance_scenario(scenario_result)
