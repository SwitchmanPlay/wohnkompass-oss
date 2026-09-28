"""The listing record every portal adapter produces."""

from __future__ import annotations

from dataclasses import dataclass

# immowelt quotes the net cold rent (Nettokaltmiete). Operating costs and VAT
# in Vienna add roughly this much per m², so alerts show "≈ warm rent".
ESTIMATED_COSTS_PER_M2 = 3.6

DEALS = ("rent", "buy")
KINDS = ("flat", "house")


@dataclass(frozen=True)
class Listing:
    source: str
    source_id: str
    url: str
    title: str
    deal: str
    kind: str
    price: float | None = None
    price_is_net: bool = False
    size_m2: float | None = None
    rooms: float | None = None
    postcode: str | None = None
    address: str | None = None
    description: str = ""
    photos: tuple[str, ...] = ()

    @property
    def id(self) -> str:
        return f"{self.source}:{self.source_id}"

    @property
    def is_buy(self) -> bool:
        return self.deal == "buy"

    @property
    def price_is_estimate(self) -> bool:
        return self.price_is_net and not self.is_buy

    @property
    def effective_price(self) -> float | None:
        """Warm rent (estimated from net rent when needed) or purchase price."""
        if self.price is None:
            return None
        if self.price_is_estimate and self.size_m2:
            return round(self.price + ESTIMATED_COSTS_PER_M2 * self.size_m2, 2)
        return self.price

    @property
    def price_per_m2(self) -> float | None:
        price = self.effective_price
        if price is None or not self.size_m2:
            return None
        return round(price / self.size_m2, 2)
