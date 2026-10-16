"""Disposable Day 26A catalog/designer/held-cart benchmark; fictional data only."""

import json
import secrets
import tempfile
from contextlib import closing
from pathlib import Path
from statistics import median
from time import perf_counter

from flask import g

from database import database as db
from services.auth import register_user
from services.held_sales import hold_sale
from services.pos_layout import defaults, save_layout
from web_app import app


def run(size=1000):
    previous_db, previous_config = db.DB_FILE, app.config.copy()
    with tempfile.TemporaryDirectory(prefix="tccp26a-benchmark-") as directory:
        try:
            db.DB_FILE = str(Path(directory) / "fictional.db")
            app.config.update(TESTING=True, DEMO_READ_ONLY=False)
            db.initialize_database(seed=False)
            password = "fictional benchmark passphrase"
            user, store = register_user(
                "pos-benchmark@example.com", password, password, "Fictional POS benchmark"
            )
            with app.test_request_context("/"):
                g.user, g.store, g.is_demo = {"id": user}, {"id": store}, False
                with closing(db.get_db_connection()) as conn, conn:
                    conn.executemany(
                        "INSERT INTO products(store_id,name,sku,price,price_cents,quantity) VALUES(?,?,?,1.25,125,1000)",
                        [
                            (store, f"Fictional product {number:04}", f"POS-{number:04}")
                            for number in range(size)
                        ],
                    )
                    ids = [
                        row["id"]
                        for row in conn.execute(
                            "SELECT id FROM products WHERE store_id=? ORDER BY id", (store,)
                        )
                    ]
                save_layout(
                    dict(
                        defaults(), preset="custom", product_order=ids[-100:], favorites=ids[:100]
                    ),
                    0,
                )
                for number in range(20):
                    hold_sale(
                        {str(identifier): 1 for identifier in ids[number : number + 10]},
                        None,
                        None,
                        "Fictional notes",
                        f"Fictional hold {number:02}",
                        secrets.token_urlsafe(32),
                    )
            client = app.test_client()
            client.get("/login")
            with client.session_transaction() as state:
                csrf = state["csrf_token"]
            assert (
                client.post(
                    "/login",
                    data={
                        "csrf_token": csrf,
                        "email": "pos-benchmark@example.com",
                        "password": password,
                    },
                ).status_code
                == 302
            )
            results = {}
            for path in (
                "/sale",
                "/sale?q=POS-0999",
                "/sale?favorites=1",
                "/pos/designer",
                "/pos/held",
            ):
                samples = []
                for _ in range(3):
                    start = perf_counter()
                    response = client.get(path)
                    assert response.status_code == 200, (path, response.status_code)
                    samples.append((perf_counter() - start) * 1000)
                results[path] = round(median(samples), 3)
            with closing(db.get_db_connection()) as conn:
                assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0
                assert conn.execute("SELECT MIN(quantity) FROM products").fetchone()[0] == 1000
                assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
                assert not conn.execute("PRAGMA foreign_key_check").fetchall()
                plan = [
                    row[3]
                    for row in conn.execute(
                        "EXPLAIN QUERY PLAN SELECT id FROM held_sales WHERE store_id=? AND state='held' ORDER BY updated_at DESC,id DESC LIMIT 20",
                        (store,),
                    )
                ]
            return dict(
                products=size,
                pinned_products=100,
                favorites=100,
                held_sales=20,
                median_ms=results,
                stock_unchanged=True,
                transactions_created=0,
                integrity="ok",
                held_query_plan=plan,
            )
        finally:
            db.DB_FILE = previous_db
            app.config.clear()
            app.config.update(previous_config)


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
