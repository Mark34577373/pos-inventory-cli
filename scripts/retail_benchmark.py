"""Disposable fictional retail benchmark; never opens the working database."""

import json
import secrets
import tempfile
from contextlib import closing
from pathlib import Path
from statistics import median
from time import perf_counter

from flask import g

from config import business_now
from database import database as db
from services.auth import register_user
from services.customers import save_customer
from services.expenses import create_expense
from services.inventory import create_product
from services.purchasing import (
    create_order,
    receive_order,
    save_order_line,
    save_supplier,
    transition_order,
)
from services.refunds import get_sale
from services.transaction_service import complete_transaction
from web_app import app


def run(size=300):
    original_db, original_config = db.DB_FILE, app.config.copy()
    with tempfile.TemporaryDirectory(prefix="tccp-retail-benchmark-") as directory:
        try:
            db.DB_FILE = str(Path(directory) / "fictional.db")
            app.config.update(TESTING=True, DEMO_READ_ONLY=False)
            db.initialize_database(seed=False)
            password = "fictional benchmark passphrase"
            user, store = register_user(
                "retail-benchmark@example.com", password, password, "Fictional retail benchmark"
            )
            day = business_now().date().isoformat()
            with app.test_request_context("/"):
                g.user, g.store, g.is_demo = {"id": user}, {"id": store}, False
                product = create_product(
                    "Benchmark SKU product", "1.00", size + 100, sku="BENCH-SKU"
                )
                customer = supplier = None
                for number in range(size):
                    customer = save_customer({"name": f"Fictional customer {number:04}"})
                    supplier = save_supplier({"name": f"Fictional supplier {number:04}"})
                    create_expense(
                        dict(
                            category="Software",
                            description=f"Fictional expense {number:04}",
                            amount="1.00",
                            expense_date=day,
                        ),
                        secrets.token_urlsafe(24),
                    )
                for number in range(size):
                    complete_transaction(
                        [{"id": product, "qty": 1}], "1.08", "Card", customer_id=customer
                    )
                for number in range(size // 3):
                    order = create_order(supplier)
                    save_order_line(order, product, 2, ".50")
                    transition_order(order, "order")
                # Receiving timing includes all stock/audit/alert work.
                from services.purchasing import get_order

                line = get_order(order)["lines"][0]["id"]
                start = perf_counter()
                receive_order(order, {line: 1}, secrets.token_urlsafe(24))
                receiving_ms = round((perf_counter() - start) * 1000, 3)
                assert get_sale(1)["transaction"]["customer_id"] == customer
            client = app.test_client()
            client.get("/login")
            with client.session_transaction() as state:
                csrf = state["csrf_token"]
            assert (
                client.post(
                    "/login",
                    data={
                        "csrf_token": csrf,
                        "email": "retail-benchmark@example.com",
                        "password": password,
                    },
                ).status_code
                == 302
            )
            timings = {}
            for path in (
                "/customers?q=Fictional",
                "/suppliers?q=Fictional",
                "/sale?q=BENCH-SKU",
                "/purchases",
                "/expenses?category=Software",
                "/financials?preset=today",
                "/financials/export/overview.csv?preset=today",
                "/customers/export.csv",
            ):
                samples = []
                for _ in range(3):
                    start = perf_counter()
                    result = client.get(path)
                    assert result.status_code == 200, (path, result.status_code)
                    samples.append((perf_counter() - start) * 1000)
                timings[path] = round(median(samples), 3)
            with closing(db.get_db_connection()) as conn:
                assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
                assert not conn.execute("PRAGMA foreign_key_check").fetchall()
                plans = [
                    row[3]
                    for row in conn.execute(
                        "EXPLAIN QUERY PLAN SELECT id FROM expenses WHERE store_id=? AND category=? AND expense_date BETWEEN ? AND ?",
                        (store, "Software", day, day),
                    )
                ]
            return dict(
                customers=size,
                suppliers=size,
                expenses=size,
                sales=size,
                purchase_orders=size // 3,
                median_ms=timings,
                receiving_ms=receiving_ms,
                expense_query_plan=plans,
                integrity="ok",
            )
        finally:
            db.DB_FILE = original_db
            app.config.clear()
            app.config.update(original_config)


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
