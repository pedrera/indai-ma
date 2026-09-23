"""Read-only Streamlit view over independently evaluated supply positions."""
from datetime import datetime, timedelta, timezone
from dataclasses import replace
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
from .operational_attention import (
    OperationalAttentionItem,
    OperationalAttentionResult,
    OperationalAttentionService,
)
from .decision_models import (
    DecisionAlternative,
    DecisionAnalysis,
    DecisionContext,
    ExplicitChange,
)
from .service import SupplyAssuranceRequest, SupplyAssuranceResult, SupplyAssuranceService
from .supply_scenarios import SupplyAssuranceAlternative, evaluate_supply_assurance_alternative


_REFERENCE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)
_CUSTOMER_LABELS = {
    "hospital-costa-sur": "Hospital Costa Sur",
    "alimentos-del-sur": "Alimentos del Sur",
}
_FINDING_LABELS = {
    "safety_stock_breach": "Safety stock breach",
    "stockout_before_delivery": "Stockout before delivery",
    "capacity_overflow": "Capacity overflow",
}
_ATTENTION_VIEWS = ("All", "Attention facts", "No attention facts", "Evaluation issues")
_WHAT_IF_TYPES = (
    "Earlier/later planned delivery",
    "Different planned delivery quantity",
    "Hypothetical consumption forecast",
)
_CHANGE_LABELS = {
    "delivery_plan.planned_delivery_at": "Planned delivery date",
    "delivery_plan.planned_quantity": "Planned delivery quantity",
    "consumption_forecast.rate": "Consumption forecast rate",
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
    return format(number.normalize(), ",f")


def _quantity_text(quantity: Quantity) -> str:
    return f"{_format_number(quantity.value)} {quantity.unit}"


def _render_attention_fact(item: OperationalAttentionItem, fact) -> None:
    """Present an existing finding alongside its original projection values."""
    projection = item.result.projection
    finding = fact.source_finding
    st.write(f"**{_FINDING_LABELS.get(finding.code, finding.code)}**")
    if projection is None:
        return
    if finding.code == "safety_stock_breach":
        st.write(
            f"Projected inventory before delivery: "
            f"{_quantity_text(projection.inventory_immediately_before_delivery)}"
        )
        st.write(f"Configured safety stock: {_quantity_text(projection.safety_stock)}")
        st.write(f"Gap: {_quantity_text(projection.safety_stock_gap_before_delivery)}")
    elif finding.code == "stockout_before_delivery" and projection.stockout_at is not None:
        st.write(f"Stockout at: {projection.stockout_at.isoformat()}")
    elif finding.code == "capacity_overflow":
        st.write(f"Installation capacity: {_quantity_text(projection.capacity)}")
        st.write(
            f"Inventory after delivery: "
            f"{_quantity_text(projection.inventory_immediately_after_delivery)}"
        )
        st.write(f"Capacity overflow: {_quantity_text(projection.capacity_overflow)}")


def _render_item(item: OperationalAttentionItem) -> None:
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
        st.markdown("**Evaluation issue**")
        st.write("Status: MISSING_INPUTS")
        st.write("Missing inputs:")
        for field in result.missing_inputs:
            st.write(f"- {_MISSING_INPUT_LABELS.get(field, field)} ({field})")
        return
    if result.status == "INVALID":
        st.markdown("**Evaluation issue**")
        st.write("Status: INVALID")
        st.write("Validation errors:")
        for error in result.validation_errors:
            st.write(f"- {error}")
        return
    if result.status != "COMPLETED" or result.projection is None:
        st.error("This item returned an inconsistent supply assurance result.")
        return

    projection = result.projection
    st.markdown("**Attention facts**")
    if item.facts:
        for fact in item.facts:
            _render_attention_fact(item, fact)
    else:
        st.write("No attention facts")

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

    st.markdown("**Assessment details**")
    st.write(f"Required delivery volume: {_quantity_text(projection.required_delivery_volume)}")
    st.write(f"Capacity exceeded: {'Yes' if projection.capacity_exceeded else 'No'}")
    st.write(f"Capacity overflow: {_quantity_text(projection.capacity_overflow)}")


def _position_label(item: OperationalAttentionItem) -> str:
    request = item.request
    site_name = request.site.name if request.site is not None else "Unknown site"
    product_name = request.gas_product.name if request.gas_product is not None else "Unknown gas"
    return f"{site_name} / {product_name} — {item.item_id}"


def _build_portfolio_what_if(
    item: OperationalAttentionItem,
    hypothesis: str,
    value,
) -> tuple[SupplyAssuranceAlternative, ExplicitChange]:
    """Build an alternative by replacing exactly one explicit request field."""
    request = item.request
    if hypothesis == _WHAT_IF_TYPES[0]:
        before = request.delivery_plan.planned_delivery_at
        after = datetime.combine(value, before.timetz())
        alternative_request = replace(
            request,
            delivery_plan=replace(request.delivery_plan, planned_delivery_at=after),
        )
        change = ExplicitChange("delivery_plan.planned_delivery_at", before, after)
        suffix = "delivery-timing"
    elif hypothesis == _WHAT_IF_TYPES[1]:
        before = request.delivery_plan.planned_quantity
        after = Quantity(value, before.unit)
        alternative_request = replace(
            request,
            delivery_plan=replace(request.delivery_plan, planned_quantity=after),
        )
        change = ExplicitChange("delivery_plan.planned_quantity", before, after)
        suffix = "delivery-quantity"
    elif hypothesis == _WHAT_IF_TYPES[2]:
        before = request.consumption_forecast.rate
        after = ConsumptionRate(value, before.quantity_unit, before.time_unit)
        alternative_request = replace(
            request,
            consumption_forecast=replace(request.consumption_forecast, rate=after),
        )
        change = ExplicitChange("consumption_forecast.rate", before, after)
        suffix = "consumption-rate"
    else:
        raise ValueError("unsupported portfolio what-if hypothesis")

    scenario_alternative = SupplyAssuranceAlternative(
        id=f"portfolio-{item.item_id}-{suffix}",
        label=hypothesis,
        alternative_request=alternative_request,
    )
    return scenario_alternative, change


def _change_value_text(value) -> str:
    if isinstance(value, Quantity):
        return _quantity_text(value)
    if isinstance(value, ConsumptionRate):
        return f"{_format_number(value.value)} {value.quantity_unit}/{value.time_unit}"
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _render_decision_result(title: str, result: SupplyAssuranceResult) -> None:
    st.markdown(f"**{title}**")
    st.write(f"Status: {result.status}")
    if result.status == "INVALID":
        st.write("Validation errors:")
        for error in result.validation_errors:
            st.write(f"- {error}")
        return
    if result.status == "MISSING_INPUTS":
        st.write("Missing inputs:")
        for name in result.missing_inputs:
            st.write(f"- {_MISSING_INPUT_LABELS.get(name, name)} ({name})")
        return
    if result.status != "COMPLETED" or result.projection is None:
        st.error("This scenario returned an inconsistent supply assurance result.")
        return

    projection = result.projection
    days = "—" if projection.days_of_supply is None else f"{projection.days_of_supply:.6f} days"
    metrics = (
        ("Days of supply", days),
        ("Consumption until delivery", _quantity_text(projection.consumption_until_delivery)),
        ("Inventory before delivery", _quantity_text(projection.inventory_immediately_before_delivery)),
        ("Configured safety stock", _quantity_text(projection.safety_stock)),
        ("Safety stock gap", _quantity_text(projection.safety_stock_gap_before_delivery)),
        ("Stockout before delivery", "Yes" if projection.stockout_before_delivery else "No"),
        ("Planned delivery", _quantity_text(projection.planned_delivery_quantity)),
        ("Inventory after delivery", _quantity_text(projection.inventory_immediately_after_delivery)),
        ("Required delivery volume", _quantity_text(projection.required_delivery_volume)),
        ("Capacity exceeded", "Yes" if projection.capacity_exceeded else "No"),
        ("Capacity overflow", _quantity_text(projection.capacity_overflow)),
    )
    for start in range(0, len(metrics), 3):
        columns = st.columns(3)
        for column, (label, value) in zip(columns, metrics[start:start + 3]):
            column.metric(label, value)
    if result.findings:
        st.write("Existing findings:")
        for finding in result.findings:
            st.write(f"- {_FINDING_LABELS.get(finding.code, finding.code)}")


def _render_portfolio_what_if(attention: OperationalAttentionResult) -> None:
    st.markdown("### Explore one explicit what-if")
    st.caption(
        "This evaluates deterministic consequences of an explicit hypothesis. "
        "It does not recommend an action."
    )
    if not attention.items:
        st.info("There are no positions available for a what-if analysis.")
        return

    labels = {item.item_id: _position_label(item) for item in attention.items}
    selected_id = st.selectbox(
        "Select one position",
        tuple(labels),
        format_func=lambda item_id: labels[item_id],
        key="supply_portfolio_what_if_item",
    )
    selected_item = next(item for item in attention.items if item.item_id == selected_id)
    if selected_item.evaluation_status != "COMPLETED" or selected_item.result.projection is None:
        st.info("What-if requires a COMPLETED baseline position.")
        return

    request = selected_item.request
    hypothesis = st.selectbox(
        "Explicit hypothesis",
        _WHAT_IF_TYPES,
        key="supply_portfolio_what_if_type",
    )
    with st.form("supply_portfolio_what_if_form"):
        if hypothesis == _WHAT_IF_TYPES[0]:
            delivery = request.delivery_plan
            reference = request.reference_time
            alternative_date = st.date_input(
                "Alternative planned delivery date",
                value=delivery.planned_delivery_at.date(),
                min_value=reference.date(),
                key="supply_portfolio_what_if_delivery_date",
            )
            explicit_value = alternative_date
        elif hypothesis == _WHAT_IF_TYPES[1]:
            quantity = request.delivery_plan.planned_quantity
            explicit_value = st.number_input(
                f"Alternative planned delivery quantity ({quantity.unit})",
                min_value=0.0,
                value=float(quantity.value),
                step=1.0,
                key="supply_portfolio_what_if_delivery_quantity",
            )
        else:
            rate = request.consumption_forecast.rate
            explicit_value = st.number_input(
                f"Alternative consumption forecast ({rate.quantity_unit}/{rate.time_unit})",
                min_value=0.0,
                value=float(rate.value),
                step=1.0,
                key="supply_portfolio_what_if_consumption_rate",
            )
        submitted = st.form_submit_button("Evaluate explicit hypothesis", type="primary")

    alternative, change = _build_portfolio_what_if(selected_item, hypothesis, explicit_value)
    if submitted:
        scenario = evaluate_supply_assurance_alternative(
            request, alternative, SupplyAssuranceService(),
        )
        analysis = DecisionAnalysis(
            context=DecisionContext(selected_item),
            alternatives=(DecisionAlternative(selected_item.item_id, change, scenario),),
        )
        st.session_state["supply_portfolio_decision_analysis"] = analysis

    analysis = st.session_state.get("supply_portfolio_decision_analysis")
    if (
        analysis is None
        or analysis.context.item_id != selected_item.item_id
        or len(analysis.alternatives) != 1
        or analysis.alternatives[0].change != change
    ):
        return
    for alternative in analysis.alternatives:
        change = alternative.change
        st.markdown("### Baseline / alternative consequences")
        st.write(
            f"Explicit change — {_CHANGE_LABELS.get(change.field_path, change.field_path)}: "
            f"{_change_value_text(change.before)} → {_change_value_text(change.after)}"
        )
        st.caption(f"Alternative position: {alternative.item_id}")
        baseline_column, alternative_column = st.columns(2)
        with baseline_column:
            _render_decision_result("BASELINE", alternative.scenario.baseline_result)
        with alternative_column:
            _render_decision_result("ALTERNATIVE", alternative.result)


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
    st.caption(
        "This view surfaces operational facts already produced by each independent supply "
        "assessment. It does not rank positions or recommend actions."
    )
    request = _canonical_portfolio_request()
    portfolio = SupplyPortfolioService().evaluate(request)
    attention: OperationalAttentionResult = OperationalAttentionService().project(portfolio)

    completed = tuple(item for item in attention.items if item.evaluation_status == "COMPLETED")
    with_facts = tuple(item for item in completed if item.facts)
    without_facts = tuple(item for item in completed if not item.facts)
    invalid_count = sum(item.evaluation_status == "INVALID" for item in attention.items)
    missing_count = sum(item.evaluation_status == "MISSING_INPUTS" for item in attention.items)
    st.markdown("**Operational attention summary**")
    summary = st.columns(5)
    for column, label, value in zip(
        summary,
        ("Total positions", "Positions with attention facts", "Positions without attention facts",
         "INVALID positions", "MISSING_INPUTS positions"),
        (len(attention.items), len(with_facts), len(without_facts), invalid_count, missing_count),
    ):
        column.metric(label, value)

    selected_view = st.selectbox("View positions", _ATTENTION_VIEWS, key="supply_portfolio_attention_filter")
    if selected_view == "Attention facts":
        visible_items = with_facts
    elif selected_view == "No attention facts":
        visible_items = without_facts
    elif selected_view == "Evaluation issues":
        visible_items = tuple(
            item for item in attention.items
            if item.evaluation_status in ("INVALID", "MISSING_INPUTS")
        )
    else:
        visible_items = attention.items

    previous_group = None
    for item in visible_items:
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

    _render_portfolio_what_if(attention)
