"""
Pricing Engine v2
- NO hard blocks — sales has freedom to set any margin
- Credit card fee: orders under $10K auto-add 3.65% CC processing
- Below-cost warning: if sell < cost, warns but allows with justification
- Payment method: Credit Card / Direct Payment / ACH
"""
from decimal import Decimal, ROUND_HALF_UP
from app.schemas import PricingLineInput, PricingLineResult, PricingRequest, PricingResponse
from app.config import get_settings

settings = get_settings()

ZERO = Decimal("0")
FOUR_PLACES = Decimal("0.0001")
CC_FEE_PCT = Decimal("3.65")  # Credit card processing fee
CC_THRESHOLD = Decimal("10000")  # Below this, assume credit card payment
GSA_IFF_PCT = Decimal("0.75")  # GSA Industrial Funding Fee (0.75% on all GSA sales)


def _d(v) -> Decimal:
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def calculate_landed_cost(unit_cost, inbound_freight, quantity, duty_rate, handling_fee):
    unit_cost = _d(unit_cost)
    freight_per_unit = (_d(inbound_freight) / max(quantity, 1)).quantize(FOUR_PLACES, ROUND_HALF_UP)
    duty_per_unit = (unit_cost * _d(duty_rate)).quantize(FOUR_PLACES, ROUND_HALF_UP)
    return (unit_cost + freight_per_unit + duty_per_unit + _d(handling_fee)).quantize(FOUR_PLACES, ROUND_HALF_UP)


def apply_forex_buffer(landed_cost, buffer_pct):
    if buffer_pct <= 0:
        return landed_cost
    return (landed_cost * (1 + _d(buffer_pct) / 100)).quantize(FOUR_PLACES, ROUND_HALF_UP)


def calculate_sell_price(cost_basis, target_margin_pct):
    margin_decimal = _d(target_margin_pct) / 100
    if margin_decimal >= 1:
        raise ValueError("Target margin must be less than 100%")
    if margin_decimal <= 0:
        # Zero or negative margin — sell at cost (sales decides)
        return cost_basis
    divisor = 1 - margin_decimal
    return (cost_basis / divisor).quantize(FOUR_PLACES, ROUND_HALF_UP)


def calculate_margin(sell_price, cost_basis):
    sell_price = _d(sell_price)
    cost_basis = _d(cost_basis)
    if sell_price <= 0:
        return 0.0
    return float(((sell_price - cost_basis) / sell_price * 100).quantize(Decimal("0.01"), ROUND_HALF_UP))


def check_guardrail(margin_pct, sell_price, cost_basis, min_margin=0.0):
    """
    NEVER blocks — only warns. Sales has full freedom.
    Returns (status, message)
    """
    sell = _d(sell_price)
    cost = _d(cost_basis)

    if cost > 0 and sell < cost:
        return "BELOW_COST", f"⚠️ Selling BELOW COST. Loss of ${float(cost - sell):.2f}/unit. Approval required."

    if margin_pct <= 0:
        return "NO_MARGIN", f"⚠️ Zero or negative margin ({margin_pct:.2f}%). Verify pricing."

    if margin_pct < 5:
        return "LOW", f"Low margin ({margin_pct:.2f}%). Credit card fee may eliminate profit."

    if margin_pct < 8:
        return "CAUTION", f"Margin {margin_pct:.2f}% — thin after CC fees on small orders."

    return "PASS", None


def apply_cc_fee(sell_price, quantity, is_cc=False):
    """
    Add 3.65% CC fee ONLY if the RFQ-level decision says CC applies.
    Decision is made in price_request based on TOTAL order value.
    """
    sell = _d(sell_price)
    if is_cc and sell > 0:
        cc_fee = (sell * CC_FEE_PCT / 100).quantize(FOUR_PLACES, ROUND_HALF_UP)
        return sell + cc_fee, cc_fee, True
    return sell, ZERO, False


def price_line(line, min_margin, soft_buffer, tax_rate=0.0, is_cc=False, is_gsa=True):
    unit_cost = _d(line.unit_cost)

    # Step 1: Landed cost (includes freight + duty + handling)
    landed = calculate_landed_cost(
        unit_cost=unit_cost,
        inbound_freight=_d(line.inbound_freight),
        quantity=line.quantity,
        duty_rate=line.duty_rate,
        handling_fee=_d(line.handling_fee),
    )

    # Step 2: Forex buffer
    buffer_pct = line.forex_buffer_pct if line.is_international else 0.0
    cost_basis = apply_forex_buffer(landed, buffer_pct)

    # Step 2.5: GSA Industrial Funding Fee (0.75% — AGT pays this to GSA on every sale)
    gsa_fee = ZERO
    if is_gsa and cost_basis > 0:
        gsa_fee = (cost_basis * GSA_IFF_PCT / 100).quantize(FOUR_PLACES, ROUND_HALF_UP)
        cost_basis = cost_basis + gsa_fee

    # Step 3: Deal registration
    deal_applied = bool(line.deal_registration_id)

    # Step 4: Sell price from target margin
    if line.target_margin_pct > 0:
        sell_price = calculate_sell_price(cost_basis, line.target_margin_pct)
    else:
        sell_price = cost_basis  # No margin — sell at cost

    # Step 5: Manual override (sales freedom)
    is_override = False
    if line.override_sell_price is not None:
        sell_price = _d(line.override_sell_price)
        is_override = True

    # Step 6: Credit card fee (decision made at RFQ level, not per line)
    sell_price, cc_fee, applied_cc = apply_cc_fee(sell_price, line.quantity, is_cc)

    # Step 7: Calculate actual margin (after CC fee if applicable)
    margin = calculate_margin(sell_price, cost_basis)

    # Step 8: Guardrail check (WARNING only, never blocks)
    status, msg = check_guardrail(margin, sell_price, cost_basis, min_margin)
    if applied_cc and cc_fee > 0:
        if msg:
            msg += f" CC fee: ${float(cc_fee):.4f}/unit added."
        else:
            msg = f"CC fee ${float(cc_fee):.4f}/unit included (3.65%)."
            status = "CC_ADJUSTED"

    if gsa_fee > 0:
        gsa_note = f" GSA IFF: ${float(gsa_fee):.4f}/unit (0.75%) included in cost."
        msg = (msg or "") + gsa_note

    # Step 9: Tax
    tax = (cost_basis * _d(tax_rate)).quantize(FOUR_PLACES, ROUND_HALF_UP) if tax_rate > 0 else ZERO
    profit = (sell_price - cost_basis).quantize(FOUR_PLACES, ROUND_HALF_UP)

    return PricingLineResult(
        bom_item_id=line.bom_item_id,
        unit_cost=unit_cost,
        landed_cost_per_unit=landed,
        cost_basis=cost_basis,
        sell_price_per_unit=sell_price,
        sell_price_total=(sell_price * line.quantity).quantize(FOUR_PLACES, ROUND_HALF_UP),
        margin_pct=margin,
        gross_profit_per_unit=profit,
        tax_per_unit=tax,
        is_override=is_override,
        guardrail_status=status,
        guardrail_message=msg,
        deal_reg_applied=deal_applied,
    )


def price_request(req):
    min_margin = req.min_margin_pct if req.min_margin_pct is not None else 0.0
    soft_buffer = settings.SOFT_MARGIN_WARNING_BUFFER
    tax_rate = 0.06 if req.is_taxable else 0.0
    payment_method = getattr(req, 'payment_method', 'auto') or 'auto'
    is_gsa = not req.is_taxable

    # ── PASS 1: Calculate all lines WITHOUT CC fee to get total RFQ value ──
    results_no_cc = []
    for line in req.lines:
        results_no_cc.append(price_line(line, min_margin, soft_buffer, tax_rate, is_cc=False, is_gsa=is_gsa))

    total_sell_no_cc = sum(r.sell_price_total for r in results_no_cc)

    # ── Decide CC at RFQ level ──
    apply_cc = False
    if payment_method == "credit_card":
        apply_cc = True
    elif payment_method == "auto" and total_sell_no_cc < CC_THRESHOLD:
        apply_cc = True
    # If payment is "direct" or "ach" → never apply CC fee

    # ── PASS 2: If CC applies, recalculate WITH CC fee ──
    if apply_cc:
        results = []
        for line in req.lines:
            results.append(price_line(line, min_margin, soft_buffer, tax_rate, is_cc=True, is_gsa=is_gsa))
    else:
        results = results_no_cc

    total_cost = sum(r.cost_basis * (req.lines[i].quantity) for i, r in enumerate(results))
    total_sell = sum(r.sell_price_total for r in results)
    total_tax = sum(r.tax_per_unit * req.lines[i].quantity for i, r in enumerate(results))
    blended = calculate_margin(_d(total_sell), _d(total_cost)) if total_sell > 0 else 0.0

    return PricingResponse(
        rfq_id=req.rfq_id,
        lines=results,
        total_cost=_d(total_cost).quantize(Decimal("0.01")),
        total_sell=_d(total_sell).quantize(Decimal("0.01")),
        total_tax=_d(total_tax).quantize(Decimal("0.01")),
        blended_margin_pct=blended,
        any_blocked=False,
    )
