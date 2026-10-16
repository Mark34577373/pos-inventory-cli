"""Durable condition episodes, updated atomically by SQLite inventory triggers."""

NOW = "strftime('%Y-%m-%dT%H:%M:%fZ','now')"
THRESHOLD = "COALESCE(p.low_stock_threshold,s.low_stock_default)"


def reconcile_sql(scope, *, product=False):
    """Internal SQL fragments only; scope is never supplied by an HTTP caller."""
    source = f"FROM products p JOIN store_settings s ON s.store_id=p.store_id WHERE {scope}"
    guard = "store_id=NEW.store_id" + (" AND product_id=NEW.id" if product else "")
    return f"""
    INSERT INTO inventory_alerts(store_id,product_id,product_name,kind,severity,quantity,threshold,resolved_at)
    SELECT p.store_id,p.id,p.name,'restored','info',p.quantity,{THRESHOLD},{NOW} {source}
      AND p.is_active=1 AND p.quantity>{THRESHOLD}
      AND EXISTS(SELECT 1 FROM inventory_alerts a WHERE a.store_id=p.store_id AND a.product_id=p.id AND a.resolved_at IS NULL);
    UPDATE inventory_alerts SET resolved_at={NOW},updated_at={NOW}
      WHERE {guard} AND resolved_at IS NULL AND EXISTS(SELECT 1 {source} AND p.id=inventory_alerts.product_id AND p.store_id=inventory_alerts.store_id)
      AND NOT EXISTS(SELECT 1 {source} AND p.id=inventory_alerts.product_id AND p.store_id=inventory_alerts.store_id AND p.is_active=1
        AND ((inventory_alerts.kind='out' AND p.quantity=0) OR (inventory_alerts.kind='low' AND p.quantity>0 AND p.quantity<={THRESHOLD})));
    UPDATE inventory_alerts SET
      quantity=(SELECT quantity FROM products WHERE id=inventory_alerts.product_id AND store_id=inventory_alerts.store_id),
      product_name=(SELECT name FROM products WHERE id=inventory_alerts.product_id AND store_id=inventory_alerts.store_id),
      threshold=(SELECT {THRESHOLD} FROM products p JOIN store_settings s ON s.store_id=p.store_id WHERE p.id=inventory_alerts.product_id AND p.store_id=inventory_alerts.store_id),
      updated_at={NOW}
      WHERE {guard} AND resolved_at IS NULL AND EXISTS(SELECT 1 {source} AND p.id=inventory_alerts.product_id AND p.store_id=inventory_alerts.store_id
        AND (p.quantity!=inventory_alerts.quantity OR p.name!=inventory_alerts.product_name OR {THRESHOLD}!=inventory_alerts.threshold));
    INSERT INTO inventory_alerts(store_id,product_id,product_name,kind,severity,quantity,threshold)
    SELECT p.store_id,p.id,p.name,CASE WHEN p.quantity=0 THEN 'out' ELSE 'low' END,
        CASE WHEN p.quantity=0 THEN 'critical' ELSE 'warning' END,p.quantity,{THRESHOLD} {source}
      AND p.is_active=1 AND p.quantity<={THRESHOLD}
      AND NOT EXISTS(SELECT 1 FROM inventory_alerts a WHERE a.store_id=p.store_id AND a.product_id=p.id AND a.resolved_at IS NULL);
    """


def migrate_alerts(conn):
    if conn.execute("SELECT 1 FROM schema_migrations WHERE version=22").fetchone():
        return
    conn.execute(f"""CREATE TABLE inventory_alerts(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        store_id INTEGER NOT NULL REFERENCES stores(id), product_id INTEGER NOT NULL,
        product_name TEXT NOT NULL,
        kind TEXT NOT NULL CHECK(kind IN ('low','out','restored')),
        severity TEXT NOT NULL CHECK((kind='low' AND severity='warning') OR (kind='out' AND severity='critical') OR (kind='restored' AND severity='info')),
        quantity INTEGER NOT NULL CHECK(typeof(quantity)='integer' AND quantity BETWEEN 0 AND 1000000),
        threshold INTEGER NOT NULL CHECK(typeof(threshold)='integer' AND threshold BETWEEN 0 AND 1000000),
        created_at TEXT NOT NULL DEFAULT({NOW}), updated_at TEXT NOT NULL DEFAULT({NOW}),
        read_at TEXT, dismissed_at TEXT, resolved_at TEXT,
        FOREIGN KEY(product_id,store_id) REFERENCES products(id,store_id),
        CHECK(kind!='restored' OR resolved_at IS NOT NULL))""")
    conn.execute(
        "CREATE UNIQUE INDEX alerts_active_product ON inventory_alerts(store_id,product_id) WHERE resolved_at IS NULL"
    )
    conn.execute("CREATE INDEX alerts_store_date ON inventory_alerts(store_id,id DESC)")
    conn.execute(
        "CREATE INDEX alerts_unread ON inventory_alerts(store_id,read_at,dismissed_at,id DESC) WHERE read_at IS NULL AND dismissed_at IS NULL"
    )
    conn.execute("CREATE INDEX alerts_store_kind ON inventory_alerts(store_id,kind,id DESC)")
    conn.execute(
        "CREATE INDEX alerts_product_history ON inventory_alerts(store_id,product_id,id DESC)"
    )
    for name, event, condition in (
        ("alerts_product_insert", "AFTER INSERT", "NEW.store_id IS NOT NULL"),
        (
            "alerts_product_update",
            "AFTER UPDATE OF quantity,low_stock_threshold,is_active,name",
            "NEW.store_id IS NOT NULL",
        ),
        (
            "alerts_product_legacy",
            "AFTER UPDATE OF store_id",
            "OLD.store_id IS NULL AND NEW.store_id IS NOT NULL",
        ),
    ):
        conn.execute(
            f"CREATE TRIGGER {name} {event} ON products WHEN {condition} BEGIN {reconcile_sql('p.id=NEW.id AND p.store_id=NEW.store_id', product=True)} END"
        )
    conn.execute(f"""CREATE TRIGGER alerts_default_threshold AFTER UPDATE OF low_stock_default ON store_settings
        WHEN NEW.low_stock_default!=OLD.low_stock_default BEGIN {reconcile_sql("p.store_id=NEW.store_id AND p.low_stock_threshold IS NULL")} END""")
    conn.execute("""CREATE TRIGGER alerts_history_guard BEFORE UPDATE ON inventory_alerts
        WHEN NEW.store_id IS NOT OLD.store_id OR NEW.product_id IS NOT OLD.product_id OR NEW.kind IS NOT OLD.kind OR NEW.severity IS NOT OLD.severity OR NEW.created_at IS NOT OLD.created_at
        OR (OLD.resolved_at IS NOT NULL AND (NEW.resolved_at IS NOT OLD.resolved_at OR NEW.quantity IS NOT OLD.quantity OR NEW.threshold IS NOT OLD.threshold OR NEW.product_name IS NOT OLD.product_name))
        OR (OLD.read_at IS NOT NULL AND NEW.read_at IS NOT OLD.read_at)
        OR (OLD.dismissed_at IS NOT NULL AND NEW.dismissed_at IS NOT OLD.dismissed_at)
        BEGIN SELECT RAISE(ABORT,'Alert history is immutable'); END""")
    conn.execute(
        "CREATE TRIGGER alerts_no_delete BEFORE DELETE ON inventory_alerts BEGIN SELECT RAISE(ABORT,'Alert history is retained'); END"
    )
    # Initial conditions, not invented historical episodes. Existing rows unchanged.
    conn.execute(f"""INSERT INTO inventory_alerts(store_id,product_id,product_name,kind,severity,quantity,threshold)
        SELECT p.store_id,p.id,p.name,CASE WHEN p.quantity=0 THEN 'out' ELSE 'low' END,
        CASE WHEN p.quantity=0 THEN 'critical' ELSE 'warning' END,p.quantity,{THRESHOLD}
        FROM products p JOIN store_settings s ON s.store_id=p.store_id WHERE p.is_active=1 AND p.quantity<={THRESHOLD}""")
    conn.execute("INSERT INTO schema_migrations(version) VALUES(22)")
