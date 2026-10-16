"""Additive Day 20 migration; never fabricates historical receipt/category data."""


def migrate_settings(conn):
    if conn.execute("SELECT 1 FROM schema_migrations WHERE version=20").fetchone():
        return
    conn.execute("""CREATE TABLE store_settings (
        store_id INTEGER PRIMARY KEY REFERENCES stores(id),
        business_email TEXT NOT NULL DEFAULT '' CHECK(length(business_email)<=254),
        phone TEXT NOT NULL DEFAULT '' CHECK(length(phone)<=80),
        address TEXT NOT NULL DEFAULT '' CHECK(length(address)<=500),
        description TEXT NOT NULL DEFAULT '' CHECK(length(description)<=1000),
        receipt_name TEXT NOT NULL CHECK(length(receipt_name) BETWEEN 1 AND 100),
        receipt_contact TEXT NOT NULL DEFAULT '' CHECK(length(receipt_contact)<=500),
        receipt_header TEXT NOT NULL DEFAULT '' CHECK(length(receipt_header)<=500),
        receipt_footer TEXT NOT NULL DEFAULT '' CHECK(length(receipt_footer)<=500),
        thank_you TEXT NOT NULL DEFAULT 'Thank you for your purchase!' CHECK(length(thank_you)<=200),
        tax_units INTEGER NOT NULL DEFAULT 82500 CHECK(typeof(tax_units)='integer' AND tax_units BETWEEN 0 AND 1000000),
        low_stock_default INTEGER NOT NULL DEFAULT 5 CHECK(typeof(low_stock_default)='integer' AND low_stock_default BETWEEN 0 AND 1000000),
        report_period TEXT NOT NULL DEFAULT 'last30' CHECK(report_period IN ('today','yesterday','last7','last30','this_month','previous_month')),
        landing_view TEXT NOT NULL DEFAULT 'overview' CHECK(landing_view IN ('overview','products','inventory')),
        show_stock INTEGER NOT NULL DEFAULT 1 CHECK(show_stock IN (0,1)),
        show_recent INTEGER NOT NULL DEFAULT 1 CHECK(show_recent IN (0,1)),
        revision INTEGER NOT NULL DEFAULT 0 CHECK(typeof(revision)='integer' AND revision>=0)
    )""")
    conn.execute("INSERT INTO store_settings(store_id,receipt_name) SELECT id,name FROM stores")
    conn.execute("""CREATE TRIGGER store_settings_defaults AFTER INSERT ON stores
        BEGIN INSERT INTO store_settings(store_id,receipt_name) VALUES(NEW.id,NEW.name); END""")
    conn.execute("""CREATE TRIGGER settings_owner_immutable BEFORE UPDATE OF store_id ON store_settings
        WHEN NEW.store_id!=OLD.store_id BEGIN SELECT RAISE(ABORT,'Settings ownership is immutable'); END""")
    conn.execute("ALTER TABLE transactions ADD COLUMN receipt_snapshot TEXT")
    conn.execute("ALTER TABLE transaction_items ADD COLUMN category_id_snapshot INTEGER")
    conn.execute("ALTER TABLE transaction_items ADD COLUMN category_name_snapshot TEXT")
    conn.execute("INSERT INTO schema_migrations(version) VALUES(20)")
