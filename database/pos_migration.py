"""Day 26A: additive presentation settings, held-sale claims and note snapshots."""


def migrate_pos(conn):
    if conn.execute("SELECT 1 FROM schema_migrations WHERE version=27").fetchone():
        return
    conn.execute("""CREATE TABLE pos_layouts(
        store_id INTEGER PRIMARY KEY REFERENCES stores(id),
        configuration TEXT NOT NULL CHECK(length(configuration)<=16000),
        revision INTEGER NOT NULL DEFAULT 0 CHECK(typeof(revision)='integer' AND revision>=0),
        updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')))""")
    conn.execute("""CREATE TABLE held_sales(
        id INTEGER PRIMARY KEY,store_id INTEGER NOT NULL REFERENCES stores(id),
        label TEXT NOT NULL CHECK(length(label) BETWEEN 1 AND 100),
        payload TEXT NOT NULL CHECK(length(payload)<=12000),
        payload_hash TEXT NOT NULL CHECK(length(payload_hash)=64),
        checkout_token TEXT NOT NULL UNIQUE CHECK(length(checkout_token) BETWEEN 20 AND 128),
        state TEXT NOT NULL DEFAULT 'held' CHECK(state IN ('held','resumed','completed','discarded')),
        claim_hash TEXT,transaction_id INTEGER,
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        UNIQUE(id,store_id),FOREIGN KEY(transaction_id,store_id) REFERENCES transactions(id,store_id),
        CHECK((state='resumed' AND claim_hash IS NOT NULL AND length(claim_hash)=64) OR (state!='resumed' AND claim_hash IS NULL)),
        CHECK((state='completed' AND transaction_id IS NOT NULL) OR (state!='completed' AND transaction_id IS NULL)))""")
    conn.execute("CREATE INDEX held_store_state ON held_sales(store_id,state,updated_at,id)")
    conn.execute(
        "ALTER TABLE transactions ADD COLUMN order_notes TEXT NOT NULL DEFAULT '' CHECK(length(order_notes)<=500)"
    )
    conn.execute("""CREATE TRIGGER sale_notes_immutable BEFORE UPDATE OF order_notes ON transactions
        WHEN NEW.order_notes!=OLD.order_notes BEGIN SELECT RAISE(ABORT,'Original sale notes are immutable'); END""")
    conn.execute("""CREATE TRIGGER pos_owner_immutable BEFORE UPDATE OF store_id ON pos_layouts
        WHEN NEW.store_id!=OLD.store_id BEGIN SELECT RAISE(ABORT,'Layout ownership is immutable'); END""")
    conn.execute("""CREATE TRIGGER held_sale_guard BEFORE UPDATE ON held_sales
        WHEN NEW.id!=OLD.id OR NEW.store_id!=OLD.store_id OR NEW.checkout_token!=OLD.checkout_token OR NEW.created_at!=OLD.created_at
        OR OLD.state IN ('completed','discarded')
        OR (NEW.state!=OLD.state AND NOT ((OLD.state='held' AND NEW.state IN ('resumed','discarded'))
            OR (OLD.state='resumed' AND NEW.state IN ('held','completed','discarded'))))
        BEGIN SELECT RAISE(ABORT,'Invalid held sale transition'); END""")
    conn.execute("""CREATE TRIGGER held_sale_no_delete BEFORE DELETE ON held_sales
        BEGIN SELECT RAISE(ABORT,'Discard held sales to preserve completion history'); END""")
    conn.execute("""CREATE TRIGGER held_completion_guard BEFORE UPDATE OF transaction_id ON held_sales
        WHEN NEW.transaction_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM transactions t
        WHERE t.id=NEW.transaction_id AND t.store_id=NEW.store_id AND t.checkout_token=NEW.checkout_token)
        BEGIN SELECT RAISE(ABORT,'Held sale completion must match its checkout'); END""")
    conn.execute("INSERT INTO schema_migrations(version) VALUES(27)")
