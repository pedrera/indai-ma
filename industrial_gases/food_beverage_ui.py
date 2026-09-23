"""Structured, deterministic Food & Beverage multi-gas supply assurance demo."""
from datetime import date, datetime, time, timedelta, timezone
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
_CUSTOMER_ID = "alimentos-del-sur"
_CUSTOMER_LABEL = "Alimentos del Sur"
_SITE_ID = "malaga-production-plant"
_SITE_LABEL = "Málaga Production Plant"
_BRANCHES = (
    {
        "key": "co2",
        "application_id": "beverage-carbonation",
        "application_name": "Beverage carbonation",
        "role": "carbonation",
        "product_id": "co2",
        "product_name": "CO2",
        "unit": "kg",
        "form": "Gas supply: CO2 / Beverage carbonation",
        "defaults": {
            "capacity": 1000.0,
            "inventory": 300.0,
            "consumption": 50.0,
            "days": 4,
            "delivery": 250.0,
            "safety_stock": 150.0,
        },
    },
    {
        "key": "n2",
        "application_id": "modified-atmosphere-inerting",
        "application_name": "Modified atmosphere / inerting",
        "role": "inerting",
        "product_id": "n2",
        "product_name": "N2",
        "unit": "Nm3",
        "form": "Gas supply: N2 / Modified atmosphere / inerting",
        "defaults": {
            "capacity": 2000.0,
            "inventory": 900.0,
            "consumption": 100.0,
            "days": 4,
            "delivery": 300.0,
            "safety_stock": 400.0,
        },
    },
)

_FINDING_LABELS = {
    "safety_stock_breach": "Projected inventory before delivery is below the configured safety stock.",
    "stockout_before_delivery": "Projected physical inventory reaches zero before the planned delivery.",
    "capacity_overflow": "Projected post-delivery inventory exceeds installation capacity.",
}
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


def _format_number(value: Decimal | int | float) -> str:
    number = value if isinstance(value, Decimal) else Decimal(str(value))
    return format(number.normalize(), "f")


def _quantity_text(quantity: Quantity) -> str:
    return f"{_format_number(quantity.value)} {quantity.unit}"


def _build_request(
    *,
    branch: dict,
    customer: Customer,
    site: Site,
    reference_time: datetime,
    capacity: float,
    inventory: float,
    consumption: float,
    days_until_delivery: int,
    planned_delivery: float,
    safety_stock: float,
) -> SupplyAssuranceRequest:
    """Construct one complete gas-specific request from explicit UI inputs."""
    unit = branch["unit"]
    product = GasProduct(branch["product_id"], branch["product_name"], "gas")
    installation_id = f"{branch['key']}-bulk-installation"
    delivery_at = reference_time + timedelta(days=days_until_delivery)
    installation = SupplyInstallation(
        installation_id,
        site.site_id,
        product.gas_product_id,
        "bulk",
        "storage_installation",
        Quantity(capacity, unit),
    )
    return SupplyAssuranceRequest(
        customer_id=customer.customer_id,
        site=site,
        application=Application(
            branch["application_id"],
            site.site_id,
            branch["application_name"],
            (ApplicationGasRequirement(product.gas_product_id, branch["role"]),),
        ),
        gas_product=product,
        installation=installation,
        inventory_snapshot=InventorySnapshot(
            installation.installation_id, reference_time, Quantity(inventory, unit),
        ),
        consumption_forecast=ConsumptionForecast(
            installation.installation_id,
            reference_time,
            delivery_at,
            ConsumptionRate(consumption, unit, "day"),
        ),
        delivery_plan=DeliveryPlan(
            installation.installation_id, delivery_at, Quantity(planned_delivery, unit),
        ),
        safety_stock=Quantity(safety_stock, unit),
        reference_time=reference_time,
    )


def _render_inputs(request: SupplyAssuranceRequest, unit: str) -> None:
    st.markdown("#### INPUTS")
    st.write(f"Current inventory: {_quantity_text(request.inventory_snapshot.inventory)}")
    st.write(
        "Forecast consumption: "
        f"{_format_number(request.consumption_forecast.rate.value)} {unit}/day"
    )
    st.write(f"Installation capacity: {_quantity_text(request.installation.capacity)}")
    st.write(f"Days until next delivery: "
             f"{(request.delivery_plan.planned_delivery_at - request.reference_time).days}")
    st.write(f"Planned delivery: {_quantity_text(request.delivery_plan.planned_quantity)}")
    st.write(f"Configured safety stock: {_quantity_text(request.safety_stock)}")


def _render_traceability(
    customer: Customer,
    request: SupplyAssuranceRequest,
    result: SupplyAssuranceResult,
) -> None:
    with st.expander("Identity and provenance"):
        st.write(f"Customer: {customer.customer_id} — {customer.name}")
        st.write(f"Site: {request.site.site_id if request.site else '—'}")
        st.write(f"Application: {request.application.application_id if request.application else '—'}")
        st.write(f"Gas product: {request.gas_product.gas_product_id if request.gas_product else '—'}")
        st.write(f"Installation: {request.installation.installation_id if request.installation else '—'}")
        unit = request.installation.capacity.unit if request.installation else "—"
        st.write(f"Operational unit: {unit}")
        if request.reference_time is not None:
            st.write(f"Reference time: {request.reference_time.isoformat()}")
        if result.projection is not None:
            rows = []
            for item in result.projection.provenance:
                value = item.value
                if isinstance(value, Quantity):
                    value = _quantity_text(value)
                elif isinstance(value, Decimal):
                    value = _format_number(value)
                rows.append({
                    "Field": item.field,
                    "Value": str(value),
                    "Origin": item.origin,
                    "Source": item.source,
                })
            st.dataframe(rows, hide_index=True, width="stretch")


def _render_branch(
    branch: dict,
    customer: Customer,
    request: SupplyAssuranceRequest,
    result: SupplyAssuranceResult,
) -> None:
    st.markdown(f"### {branch['product_name']} · {branch['unit']}")
    _render_inputs(request, branch["unit"])
    st.markdown("#### CALCULATED RESULTS")
    st.caption(f"Status: {result.status}")
    if result.status == "MISSING_INPUTS":
        st.write("Missing inputs:")
        for name in result.missing_inputs:
            st.write(f"- {_MISSING_INPUT_LABELS.get(name, name)}")
        _render_traceability(customer, request, result)
        return
    if result.status == "INVALID":
        st.write("Validation errors:")
        for error in result.validation_errors:
            st.write(f"- {error}")
        _render_traceability(customer, request, result)
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
        ("Stockout before delivery", "Yes" if projection.stockout_before_delivery else "No"),
        ("Planned delivery", _quantity_text(projection.planned_delivery_quantity)),
        ("Inventory after delivery", _quantity_text(projection.inventory_immediately_after_delivery)),
        ("Minimum quantity needed at delivery", _quantity_text(projection.required_delivery_volume)),
        ("Capacity exceeded", "Yes" if projection.capacity_exceeded else "No"),
        ("Overflow", _quantity_text(projection.capacity_overflow)),
    )
    for offset in range(0, len(values), 2):
        columns = st.columns(2)
        for column, (label, value) in zip(columns, values[offset:offset + 2]):
            column.metric(label, value)
    st.caption(
        "Minimum quantity needed at the planned-delivery instant to restore "
        "configured safety stock under the current forecast; it is not an "
        "additional quantity beyond the planned delivery."
    )
    st.caption("Below safety stock is not the same as a physical stockout.")
    st.markdown("#### FINDINGS")
    if result.findings:
        for finding in result.findings:
            st.write(f"- {_FINDING_LABELS.get(finding.code, finding.code)}")
    else:
        st.write("No findings reported.")
    _render_traceability(customer, request, result)


def render_food_beverage_supply_assurance() -> None:
    """Render the independent CO2 and N2 structured supply assurance demo."""
    st.header("Food & Beverage Supply Assurance")
    st.caption(
        "Each gas is evaluated independently using its own installation, "
        "operational unit and supply inputs. Demo values are editable examples."
    )

    with st.form("food_beverage_supply_assurance_form"):
        st.markdown("### SHARED CUSTOMER AND SITE")
        st.write(f"Customer: {_CUSTOMER_LABEL}")
        st.write(f"Site: {_SITE_LABEL}")
        reference_date = st.date_input(
            "Reference date (UTC)", value=_REFERENCE_TIME.date(),
            key="food_beverage_reference_date",
        )
        reference_clock = st.time_input(
            "Reference time (UTC)", value=time(0, 0), key="food_beverage_reference_clock",
        )

        st.markdown("### STRUCTURED SUPPLY INPUTS")
        columns = st.columns(2)
        inputs_by_gas = {}
        for column, branch in zip(columns, _BRANCHES):
            defaults = branch["defaults"]
            key = branch["key"]
            with column:
                st.markdown(f"#### {branch['form']}")
                inputs_by_gas[key] = {
                    "capacity": st.number_input(
                        f"Installation capacity ({branch['unit']})", min_value=1.0,
                        value=defaults["capacity"], step=100.0,
                        key=f"food_beverage_{key}_capacity",
                    ),
                    "inventory": st.number_input(
                        f"Current inventory ({branch['unit']})", min_value=0.0,
                        value=defaults["inventory"], step=50.0,
                        key=f"food_beverage_{key}_inventory",
                    ),
                    "consumption": st.number_input(
                        f"Forecast consumption ({branch['unit']}/day)", min_value=0.0,
                        value=defaults["consumption"], step=10.0,
                        key=f"food_beverage_{key}_consumption",
                    ),
                    "days_until_delivery": st.number_input(
                        "Days until next delivery", min_value=0, value=defaults["days"],
                        step=1, key=f"food_beverage_{key}_days",
                    ),
                    "planned_delivery": st.number_input(
                        f"Planned delivery quantity ({branch['unit']})", min_value=0.0,
                        value=defaults["delivery"], step=50.0,
                        key=f"food_beverage_{key}_delivery",
                    ),
                    "safety_stock": st.number_input(
                        f"Safety stock ({branch['unit']})", min_value=0.0,
                        value=defaults["safety_stock"], step=50.0,
                        key=f"food_beverage_{key}_safety_stock",
                    ),
                }
        submitted = st.form_submit_button("Analyze both gas supplies", type="primary")

    if submitted:
        reference_time = datetime.combine(reference_date, reference_clock, tzinfo=timezone.utc)
        customer = Customer(_CUSTOMER_ID, _CUSTOMER_LABEL)
        site = Site(_SITE_ID, customer.customer_id, _SITE_LABEL)
        requests = {
            branch["key"]: _build_request(
                branch=branch,
                customer=customer,
                site=site,
                reference_time=reference_time,
                **inputs_by_gas[branch["key"]],
            )
            for branch in _BRANCHES
        }
        service = SupplyAssuranceService()
        results = {
            branch["key"]: service.assess(requests[branch["key"]])
            for branch in _BRANCHES
        }
        st.session_state.food_beverage_supply_requests = requests
        st.session_state.food_beverage_supply_results = results
        st.session_state.food_beverage_supply_customer = customer

    requests = st.session_state.get("food_beverage_supply_requests")
    results = st.session_state.get("food_beverage_supply_results")
    customer = st.session_state.get("food_beverage_supply_customer")
    if requests is None or results is None or customer is None:
        st.info("Enter the structured inputs and run the two independent gas assessments.")
        return

    st.markdown("### INDEPENDENT GAS RESULTS")
    columns = st.columns(2)
    for column, branch in zip(columns, _BRANCHES):
        with column:
            _render_branch(
                branch,
                customer,
                requests[branch["key"]],
                results[branch["key"]],
            )
