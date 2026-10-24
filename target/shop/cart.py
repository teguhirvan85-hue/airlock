"""Cart totals for Kedai, a tiny demo shop.

Prices are in cents so totals never pick up float rounding.
"""

from dataclasses import dataclass, field

FREE_SHIPPING_FROM = 50_000  # cents
SHIPPING_FEE = 4_900


@dataclass
class Line:
    sku: str
    unit_price: int
    qty: int = 1

    @property
    def subtotal(self) -> int:
        return self.unit_price * self.qty


@dataclass
class Cart:
    lines: list[Line] = field(default_factory=list)
    coupon_percent: int = 0

    def add(self, sku: str, unit_price: int, qty: int = 1) -> None:
        if qty < 1:
            raise ValueError("qty must be at least 1")
        self.lines.append(Line(sku, unit_price, qty))

    def items_total(self) -> int:
        return sum(line.subtotal for line in self.lines)

    def discount(self) -> int:
        return self.items_total() * self.coupon_percent // 100

    def shipping(self) -> int:
        # Free shipping is decided on what the customer pays for the items.
        if self.items_total() >= FREE_SHIPPING_FROM:
            return 0
        return SHIPPING_FEE

    def total(self) -> int:
        return self.items_total() - self.discount() + self.shipping()
