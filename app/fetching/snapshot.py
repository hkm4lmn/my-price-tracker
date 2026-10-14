from dataclasses import dataclass


@dataclass(frozen=True)
class Snapshot:
    """What a shop plug-in read from one product page.

    price_sen is None only when the product is out of stock and the page shows
    no price; the tracker then keeps the last known price.
    """

    title: str
    price_sen: int | None
    in_stock: bool
    currency: str
    image_url: str | None
