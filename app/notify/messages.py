"""The text of each alert. Everything from shops is HTML-escaped."""

from html import escape

from app.money import format_sen


def render(kind: str, payload: dict, *, base_url: str) -> str:
    # Titles only appear as element text, never inside an attribute.
    title = f"<b>{escape(payload['product_title'], quote=False)}</b>"
    link = f"{base_url.rstrip('/')}/p/{payload['public_id']}"

    def price(key: str) -> str:
        return format_sen(payload[key])

    if kind == "tracking_started":
        stock = "" if payload.get("in_stock", True) else " (out of stock)"
        return f"✅ Now tracking {title}. Current price: {price('new_price_sen')}{stock}.\nHistory: {link}"
    if kind == "price_drop":
        return f"📉 {title}: {price('old_price_sen')} → {price('new_price_sen')}\n{link}"
    if kind == "target_reached":
        return (
            f"🎯 {title} is {price('new_price_sen')}, at or below your target of "
            f"{price('target_price_sen')}.\n{link}"
        )
    if kind == "back_in_stock":
        return f"📦 {title} is back in stock at {price('new_price_sen')}.\n{link}"
    if kind == "failing":
        return f"⚠️ I can't read the price for {title} right now. I'll keep trying."
    if kind == "dead":
        return (
            f"❌ I've stopped tracking {title} after 7 days of failed checks. "
            "Send the link again to retry."
        )
    raise ValueError(f"unknown notification kind: {kind}")
