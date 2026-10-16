"""Before-tax discounts with bounded, deterministic original-unit allocation.

Each unit retains at least one cent, matching the positive refund ledger.
Allocation weights are the unit's discountable cents (retail price minus one).
No unit arrays are expanded, even for million-unit quantities.
"""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from services.money import parse_cash


def discount_amount(lines, kind="none", value=""):
    subtotal = sum(line["price_cents"] * line["quantity"] for line in lines)
    capacity = sum((line["price_cents"] - 1) * line["quantity"] for line in lines)
    if kind == "none":
        if value not in ("", "0", None):
            raise ValueError("Choose a discount type before entering an amount.")
        return 0
    if kind not in ("fixed", "percent") or not isinstance(value, str) or len(value) > 30:
        raise ValueError("Choose a fixed amount or percentage discount.")
    try:
        number = Decimal(value)
        if not number.is_finite() or number < 0:
            raise ValueError
        if kind == "fixed":
            amount = parse_cash(number)
        else:
            if number > 100 or number.as_tuple().exponent < -4:
                raise ValueError
            amount = int(
                (Decimal(subtotal) * number / 100).quantize(Decimal(1), rounding=ROUND_HALF_UP)
            )
    except (InvalidOperation, ValueError, ArithmeticError):
        raise ValueError(
            "Enter a valid nonnegative discount (percentage supports four decimals)."
        ) from None
    if amount > capacity:
        raise ValueError(
            "Discount must leave at least $0.01 per unit; free-item sales are not supported."
        )
    return amount


def allocate_discount(lines, total):
    capacity = sum((line["price_cents"] - 1) * line["quantity"] for line in lines)
    if not isinstance(total, int) or isinstance(total, bool) or not 0 <= total <= capacity:
        raise ValueError("Invalid discount allocation.")
    remaining = total
    for line in lines:
        base, remainder = (
            divmod(total * (line["price_cents"] - 1), capacity) if capacity else (0, 0)
        )
        line.update(discount_base_cents=base, discount_bonus_units=0, discount_remainder=remainder)
        remaining -= base * line["quantity"]
    for line in sorted(lines, key=lambda row: (-row["discount_remainder"], row["id"])):
        bonus = min(remaining, line["quantity"])
        line["discount_bonus_units"] = bonus
        remaining -= bonus
    assert remaining == 0
    for line in lines:
        line["discount_cents"] = (
            line["discount_base_cents"] * line["quantity"] + line["discount_bonus_units"]
        )
    return lines


def refund_segments(lines, subtotal, tax):
    """Allocate original tax across paid unit prices, stable by line/ordinal."""
    groups = []
    remaining = tax
    for line in lines:
        bonus = line.get("discount_bonus_units", 0)
        paid = line["price_cents"] - line.get("discount_base_cents", 0)
        line["paid_segments"] = []
        for start, end, price in ((0, bonus, paid - 1), (bonus, line["quantity"], paid)):
            if end <= start:
                continue
            unit_tax, remainder = divmod(tax * price, subtotal)
            group = dict(
                start=start,
                end=end,
                price=price,
                unit_tax=unit_tax,
                bonus=0,
                remainder=remainder,
                line_id=line["id"],
            )
            line["paid_segments"].append(group)
            groups.append(group)
            remaining -= unit_tax * (end - start)
    for group in sorted(groups, key=lambda row: (-row["remainder"], row["line_id"], row["start"])):
        group["bonus"] = min(remaining, group["end"] - group["start"])
        remaining -= group["bonus"]
    assert remaining == 0


def refund_unit_range(line, start, quantity):
    subtotal = tax = 0
    for group in line["paid_segments"]:
        lower, upper = max(start, group["start"]), min(start + quantity, group["end"])
        if upper > lower:
            count = upper - lower
            subtotal += count * group["price"]
            tax += count * group["unit_tax"] + max(
                0, min(upper, group["start"] + group["bonus"]) - lower
            )
    return subtotal, tax
