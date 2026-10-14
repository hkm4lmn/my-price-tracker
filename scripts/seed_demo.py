"""Insert three demo products with 30 days of price history, for screenshots.

Local development only:
    DATABASE_URL=postgresql+psycopg://tracker:tracker@localhost:5432/tracker python -m scripts.seed_demo
"""

import random
import sys
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.config import settings
from app.db import make_engine, make_session_factory
from app.models import PricePoint, Product

DEMO = [
    ("https://demo.example/air-fryer", "Philips Essential Air Fryer 4.1L", 39900, True),
    ("https://demo.example/headphones", "Sony WH-1000XM6 Wireless Headphones", 179900, True),
    ("https://demo.example/kettle", "Pensonic Stainless Steel Kettle 1.7L", 8990, False),
]


def main() -> None:
    if "localhost" not in settings.database_url and "--force" not in sys.argv:
        raise SystemExit("Refusing to seed a non-local database. Pass --force if you really mean it.")

    now = datetime.now(UTC)
    rng = random.Random(7)
    session_factory = make_session_factory(make_engine(settings.database_url))
    with session_factory() as session:
        for url, title, start, in_stock in DEMO:
            if session.scalar(select(Product).where(Product.url == url)):
                continue
            product = Product(url=url, shop="schema_org", title=title, next_check_at=now + timedelta(days=365))
            session.add(product)
            session.flush()
            price, stock = start, True
            when = now - timedelta(days=30)
            session.add(PricePoint(product_id=product.id, price_sen=price, in_stock=stock, observed_at=when))
            while True:
                when += timedelta(hours=rng.choice([30, 52, 75, 110]))
                if when >= now - timedelta(hours=3):
                    break
                price = max(100, round(price * rng.choice([0.85, 0.92, 0.95, 1.04, 1.1]) / 10) * 10)
                session.add(PricePoint(product_id=product.id, price_sen=price, in_stock=stock, observed_at=when))
            if not in_stock:
                session.add(PricePoint(product_id=product.id, price_sen=price, in_stock=False, observed_at=when))
            product.current_price_sen, product.in_stock = price, in_stock
            product.last_success_at = product.last_checked_at = now - timedelta(hours=3)
            print(f"{title}: /p/{product.public_id}")
        session.commit()


if __name__ == "__main__":
    main()
