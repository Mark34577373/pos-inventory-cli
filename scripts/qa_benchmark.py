"""Fictional, disposable local QA benchmark. Never opens the working database.

Run from repository root: PYTHONPATH=. python scripts/qa_benchmark.py
"""

import json
import secrets
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from datetime import timedelta
from pathlib import Path
from statistics import median

from flask import g

from config import business_now
from database import database as db
from services.alerts import notification_summary
from services.auth import register_user
from services.inventory import create_product
from services.refunds import get_sale, process_refund
from services.transaction_service import complete_transaction
from web_app import app


@contextmanager
def identity(user, store):
    with app.test_request_context("/"):
        g.user, g.store, g.is_demo = {"id": user}, {"id": store}, False
        yield


def measure(function):
    values = []
    for _ in range(3):
        start = time.perf_counter()
        result = function()
        if hasattr(result, "status_code"):
            assert result.status_code == 200, result.status_code
        values.append((time.perf_counter() - start) * 1000)
    return round(median(values), 3)


def scenario(products, sales):
    previous_db, previous_config = db.DB_FILE, app.config.copy()
    password = "fictional benchmark passphrase"
    with tempfile.TemporaryDirectory(prefix="tccp-qa-") as directory:
        try:
            db.DB_FILE = str(Path(directory) / "fictional.db")
            app.config.update(TESTING=True, DEMO_READ_ONLY=False)
            db.initialize_database(seed=False)
            user, store = register_user(
                "benchmark@example.com", password, password, "Fictional benchmark"
            )
            day = business_now().date()
            timestamp = day.isoformat() + " 10:00:00"
            seed_start = time.perf_counter()
            with identity(user, store), closing(db.get_db_connection()) as conn, conn:
                conn.execute("BEGIN IMMEDIATE")
                ids = []
                for number in range(products):
                    quantity = (
                        10000
                        if number == 0
                        else (0 if number % 3 == 0 else (3 if number % 3 == 1 else 100))
                    )
                    ids.append(
                        conn.execute(
                            "INSERT INTO products(name,price,price_cents,quantity,store_id) VALUES(?,1,100,?,?)",
                            (f"Fictional benchmark {number}", quantity, store),
                        ).lastrowid
                    )
                for number in range(sales):
                    tx = conn.execute(
                        "INSERT INTO transactions(timestamp,total,total_cents,payment_method,tax_rate_percent,store_id) VALUES(?,1.08,108,'Card','8.25',?)",
                        (timestamp, store),
                    ).lastrowid
                    conn.execute(
                        "INSERT INTO transaction_items(transaction_id,product_id,quantity,price,price_cents,product_name,category_name_snapshot,store_id) VALUES(?,?,1,1,100,'Fictional benchmark 0','Uncategorized',?)",
                        (tx, ids[0], store),
                    )
                    conn.audit_context.update(
                        type="checkout", reason="Fictional benchmark sale", transaction_id=tx
                    )
                    conn.execute(
                        "UPDATE products SET quantity=quantity-1 WHERE id=? AND store_id=?",
                        (ids[0], store),
                    )
            seed_ms = round((time.perf_counter() - seed_start) * 1000, 3)
            client = app.test_client()
            client.get("/login")
            with client.session_transaction() as state:
                csrf = state["csrf_token"]
            assert (
                client.post(
                    "/login",
                    data={
                        "csrf_token": csrf,
                        "email": "benchmark@example.com",
                        "password": password,
                    },
                ).status_code
                == 302
            )
            interval = f"start_date={day - timedelta(days=6)}&end_date={day}"
            paths = {
                "dashboard": "/",
                "inventory_search": "/inventory?q=benchmark&status=low",
                "transaction_page": "/transactions?page=2",
                "analytics": "/analytics?" + interval,
                "csv": "/analytics/export/performance.csv?" + interval,
                "notification_filter": "/notifications?kind=low&read=unread",
            }
            timings = {
                name: measure(lambda path=path: client.get(path)) for name, path in paths.items()
            }
            with identity(user, store):
                timings["notification_summary"] = measure(notification_summary)
                pid = create_product("Fictional concurrent stock", "1", 2)

            def buy(number):
                with identity(user, store):
                    try:
                        return complete_transaction(
                            [{"id": pid, "qty": 1}],
                            "1.08",
                            "Card",
                            checkout_token=f"fictional-concurrent-{number}",
                        )
                    except ValueError:
                        return None

            start = time.perf_counter()
            with ThreadPoolExecutor(max_workers=4) as pool:
                outcomes = list(pool.map(buy, range(4)))
            timings["four_concurrent_buyers"] = round((time.perf_counter() - start) * 1000, 3)
            success = [value for value in outcomes if value is not None]
            assert len(success) == 2
            with identity(user, store):
                line = get_sale(success[0])["items"][0]["id"]
            key = secrets.token_urlsafe(24)

            def refund(_):
                with identity(user, store):
                    return process_refund(
                        success[0], {line: 1}, "Fictional duplicate refund", True, key
                    )

            start = time.perf_counter()
            with ThreadPoolExecutor(max_workers=2) as pool:
                refunds = list(pool.map(refund, range(2)))
            timings["two_duplicate_refunds"] = round((time.perf_counter() - start) * 1000, 3)
            assert refunds[0] == refunds[1]
            with closing(db.get_db_connection()) as conn:
                assert (
                    conn.execute("SELECT quantity FROM products WHERE id=?", (pid,)).fetchone()[0]
                    == 1
                )
                assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
                assert not conn.execute("PRAGMA foreign_key_check").fetchall()
                plans = {
                    name: [r[3] for r in conn.execute("EXPLAIN QUERY PLAN " + sql, (store,))]
                    for name, sql in {
                        "unread": "SELECT COUNT(*) FROM inventory_alerts WHERE store_id=? AND read_at IS NULL AND dismissed_at IS NULL",
                        "kind": "SELECT * FROM inventory_alerts WHERE store_id=? AND kind='low' ORDER BY id DESC LIMIT 20",
                    }.items()
                }
            return dict(
                products=products,
                sales=sales,
                seed_ms=seed_ms,
                median_ms=timings,
                successful_buyers=len(success),
                duplicate_refund_rows=1,
                integrity="ok",
                foreign_key_violations=0,
                query_plans=plans,
            )
        finally:
            db.DB_FILE = previous_db
            app.config.clear()
            app.config.update(previous_config)


if __name__ == "__main__":
    print(
        json.dumps([scenario(*size) for size in ((30, 100), (300, 1000), (1500, 3000))], indent=2)
    )
