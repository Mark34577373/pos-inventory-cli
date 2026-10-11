from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

_CENTS = Decimal("100")
_ONE_CENT = Decimal("1")


def to_cents(amount):
    """Convert a dollar amount to integer cents using half-up rounding."""
    try:
        value = Decimal(str(amount))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError("Amount must be a valid number.") from error

    if not value.is_finite():
        raise ValueError("Amount must be finite.")

    return int((value * _CENTS).quantize(_ONE_CENT, rounding=ROUND_HALF_UP))


def from_cents(amount_cents):
    """Return an exact Decimal dollar amount from integer cents."""
    return Decimal(amount_cents) / _CENTS


def format_cents(amount_cents):
    """Format integer cents as a currency string."""
    return f"${from_cents(amount_cents):,.2f}"


def calculate_tax_cents(subtotal_cents, tax_rate):
    """Calculate tax in cents, rounded half up to the nearest cent."""
    rate = Decimal(str(tax_rate))
    tax = (Decimal(subtotal_cents) * rate).quantize(
        _ONE_CENT, rounding=ROUND_HALF_UP
    )
    return int(tax)
