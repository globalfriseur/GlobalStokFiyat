from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

import sync_activeshop_to_plenty as sync


def round_price_two_decimals(value: float) -> float:
    """Round monetary values to 2 decimals using commercial half-up rounding."""
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


# Plenty purchase and sales prices must be written with 2 decimal places.
sync.round_price = round_price_two_decimals


if __name__ == "__main__":
    raise SystemExit(sync.main())
