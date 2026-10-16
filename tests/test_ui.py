"""Day 18 HTML/HTTP integration checks, without claiming browser visual QA."""

import re
import tempfile
import unittest
from contextlib import closing, contextmanager
from html import unescape
from html.parser import HTMLParser
from pathlib import Path

from auth_helpers import PASSWORD, login
from flask import g

from database import database as db
from manage import check_database, seed_demo
from services.alerts import list_alerts
from services.auth import register_user
from services.inventory import create_product
from services.inventory_management import save_category
from services.refunds import get_sale
from web_app import app


class Element:
    def __init__(self, tag, attrs, parent):
        self.tag, self.attrs, self.parent = tag, dict(attrs), parent
        self.text = ""

    def inside(self, tag, css_class=None):
        node = self.parent
        while node:
            if node.tag == tag and (
                css_class is None or css_class in node.attrs.get("class", "").split()
            ):
                return True
            node = node.parent
        return False


class Document(HTMLParser):
    VOID = {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }

    def __init__(self, html):
        super().__init__()
        self.nodes, self.stack, self.errors = [], [], []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = Element(tag, attrs, self.stack[-1] if self.stack else None)
        self.nodes.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_endtag(self, tag):
        if self.stack and self.stack[-1].tag == tag:
            self.stack.pop()
        else:
            self.errors.append(tag)

    def handle_data(self, value):
        for node in self.stack:
            node.text += value

    def find(self, tag=None):
        return [node for node in self.nodes if tag is None or node.tag == tag]


class UITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_db = db.DB_FILE
        self.original_config = app.config.copy()
        db.DB_FILE = str(Path(self.tmp.name) / "ui.db")
        db.initialize_database(seed=False)
        app.config.update(TESTING=True, DEMO_READ_ONLY=False)
        self.user, self.store = register_user(
            "ui-alpha@example.com", PASSWORD, PASSWORD, "Alpha <Shop>"
        )
        with self.context():
            self.category = save_category("Alpha Category")
            self.product = create_product("Alpha <Product>", "1.00", 10, category_id=self.category)
        self.client = app.test_client()
        login(self.client, "ui-alpha@example.com")
        self.client.get("/sale")
        self.post("/sale/add", {"product_id": self.product, "quantity": 2})
        with self.client.session_transaction() as state:
            key = state["checkout_token"]
        self.post("/sale/checkout", {"payment_method": "Card", "checkout_token": key})
        with self.client.session_transaction() as state:
            self.tx = state["last_receipt_id"]
        with self.context():
            self.line = get_sale(self.tx)["items"][0]["id"]
            create_product("Alpha notification example", "1.00", 0)
            self.alert = list_alerts({})["alerts"][0]["id"]

    def tearDown(self):
        db.DB_FILE = self.original_db
        app.config.clear()
        app.config.update(self.original_config)
        self.tmp.cleanup()

    @contextmanager
    def context(self, user=None, store=None):
        with app.test_request_context("/"):
            g.user = {"id": self.user if user is None else user}
            g.store = {"id": self.store if store is None else store}
            g.is_demo = False
            yield

    def post(self, path, data=None, client=None):
        client = client or self.client
        with client.session_transaction() as state:
            token = state["csrf_token"]
        return client.post(path, data=dict(data or {}, csrf_token=token))

    def paths(self):
        return [
            "/",
            "/inventory",
            "/products/new",
            f"/products/{self.product}/edit",
            "/inventory/categories",
            "/inventory/history",
            f"/inventory/adjust/{self.product}",
            "/sale",
            "/transactions",
            f"/transactions/{self.tx}",
            f"/transactions/{self.tx}/receipt",
            f"/transactions/{self.tx}/refund",
            "/analytics",
            "/reports",
            "/account",
            "/settings",
            "/notifications",
            f"/notifications/{self.alert}",
            "/missing",
        ]

    def test_all_pages_semantic_forms_tables_and_no_broken_markup(self):
        self.client.get("/sale")
        self.post("/sale/add", {"product_id": self.product, "quantity": 1})
        for path in self.paths():
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertIn(response.status_code, (200, 404))
                document = Document(response.data.decode())
                self.assertEqual(document.errors, [])
                self.assertEqual(document.stack, [])
                self.assertEqual(len(document.find("main")), 1)
                self.assertEqual(len(document.find("h1")), 1)
                ids = [node.attrs["id"] for node in document.nodes if "id" in node.attrs]
                self.assertEqual(len(ids), len(set(ids)))
                labels = {node.attrs.get("for") for node in document.find("label")}
                for control in document.nodes:
                    if (
                        control.tag in ("input", "select", "textarea")
                        and control.attrs.get("type") != "hidden"
                    ):
                        self.assertTrue(
                            control.attrs.get("id") in labels
                            or control.inside("label")
                            or control.attrs.get("aria-label"),
                            control.attrs,
                        )
                for table in document.find("table"):
                    self.assertTrue(
                        table.inside("div", "table-wrap")
                        or table.inside("div", "inventory-table-wrap")
                    )
                for heading in document.find("th"):
                    self.assertEqual(heading.attrs.get("scope"), "col")
                for form in document.find("form"):
                    self.assertFalse(form.inside("form"))
                    if form.attrs.get("method", "get").lower() == "post":
                        self.assertTrue(
                            any(
                                node.attrs.get("name") == "csrf_token"
                                and node.parent is not None
                                and self.belongs(node, form)
                                for node in document.find("input")
                            )
                        )
                self.assertIn("no-store", response.headers["Cache-Control"])

    @staticmethod
    def belongs(node, parent):
        while node.parent:
            node = node.parent
            if node is parent:
                return True
        return False

    def test_navigation_targets_active_groups_and_anonymous_navigation(self):
        for path in (
            "/inventory/categories",
            "/inventory/history",
            "/sale",
            "/transactions",
            "/analytics",
            "/reports",
        ):
            doc = Document(self.client.get(path).data.decode())
            active = [node for node in doc.find("a") if node.attrs.get("aria-current") == "page"]
            self.assertEqual(len(active), 1)
            for link in doc.find("a"):
                if link.inside("nav") and link.attrs.get("href", "").startswith("/"):
                    self.assertEqual(self.client.get(link.attrs["href"]).status_code, 200)
        anonymous = app.test_client()
        doc = Document(anonymous.get("/login").data.decode())
        nav_hrefs = [node.attrs["href"] for node in doc.find("a") if node.inside("nav")]
        self.assertEqual(set(nav_hrefs), {"/login", "/register"})
        self.assertIn("Skip to main content", anonymous.get("/login").text)

    def test_public_demo_hides_writes_and_direct_posts_still_fail(self):
        seed_demo(allow_existing=True)
        demo = app.test_client()
        demo.get("/login")
        self.post("/demo", client=demo)
        demo.get("/")
        for path in (
            "/",
            "/inventory",
            "/inventory/categories",
            "/sale",
            "/products/new",
            "/transactions",
            "/reports",
            "/analytics",
        ):
            response = demo.get(path)
            self.assertEqual(response.status_code, 200)
            doc = Document(response.text)
            actions = [
                form.attrs.get("action", path)
                for form in doc.find("form")
                if form.attrs.get("method", "get").lower() == "post"
            ]
            self.assertEqual(actions, ["/logout"], path)
            self.assertFalse(
                any(node.attrs.get("href") == "/products/new" for node in doc.find("a"))
            )
            self.assertNotIn("Alpha &lt;", response.text)
        with closing(db.get_db_connection()) as conn:
            before = [tuple(row) for row in conn.execute("SELECT * FROM products")]
        self.assertEqual(
            self.post("/sale/add", {"product_id": self.product, "quantity": 1}, demo).status_code,
            403,
        )
        self.assertEqual(
            self.post(
                f"/transactions/{self.tx}/refund", {"mode": "full", "reason": "Attack"}, demo
            ).status_code,
            403,
        )
        with closing(db.get_db_connection()) as conn:
            self.assertEqual(before, [tuple(row) for row in conn.execute("SELECT * FROM products")])

    def test_readonly_owner_no_mutation_controls_but_logout_and_exports_remain(self):
        app.config["DEMO_READ_ONLY"] = True
        for path in self.paths()[:-1]:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            doc = Document(response.text)
            for form in doc.find("form"):
                if form.attrs.get("method", "get").lower() == "post":
                    self.assertEqual(form.attrs.get("action"), "/logout")
        self.assertEqual(self.client.get("/analytics/export/summary.csv").status_code, 200)
        self.assertEqual(
            self.post("/products/new", {"name": "No", "price": 1, "quantity": 1}).status_code, 403
        )

    def test_errors_preserve_safe_inputs_and_not_passwords(self):
        response = self.post(
            f"/inventory/adjust/{self.product}",
            {"amount": "-1", "reason": "Keep this reason", "type": "restock"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Keep this reason", response.text)
        self.assertIn('value="-1"', response.text)
        self.assertIn("error-summary", response.text)
        response = self.post(
            f"/transactions/{self.tx}/refund",
            {f"qty_{self.line}": "99", "reason": "Keep refund reason", "restock": "on"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Keep refund reason", response.text)
        self.assertIn('name="restock" checked', response.text)
        public = app.test_client()
        public.get("/login")
        response = self.post(
            "/login", {"email": "nobody@example.com", "password": "fictional-wrong-secret"}, public
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("nobody@example.com", response.text)
        self.assertNotIn("fictional-wrong-secret", response.text)
        self.assertIn('data-password-toggle="password"', response.text)

    def test_http_errors_have_shared_accessible_ui_and_preserve_codes(self):
        for path, status in (("/inventory?page=bad", 400), ("/logout", 405), ("/missing", 404)):
            response = self.client.get(path)
            self.assertEqual(response.status_code, status)
            self.assertIn("design-system.css", response.text)
            self.assertIn("main-content", response.text)
            self.assertNotIn("Traceback", response.text)
        self.assertIn("POST", self.client.get("/logout").headers["Allow"])
        self.assertEqual(self.client.post("/sale/add", data={}).status_code, 400)
        self.assertTrue(check_database()[0])

    def test_three_accounts_render_only_their_own_data_and_actions(self):
        accounts = [(self.client, self.user, self.store, self.product, self.tx, "Alpha")]
        for label in ("Bravo", "Charlie"):
            user, store = register_user(
                label.lower() + "-ui@example.com", PASSWORD, PASSWORD, label + " Shop"
            )
            with self.context(user, store):
                category = save_category(label + " Category")
                product = create_product(label + " Product", "1.00", 10, category_id=category)
            client = app.test_client()
            login(client, label.lower() + "-ui@example.com")
            client.get("/sale")
            self.post("/sale/add", {"product_id": product, "quantity": 1}, client)
            with client.session_transaction() as state:
                token = state["checkout_token"]
            self.post("/sale/checkout", {"checkout_token": token, "payment_method": "Card"}, client)
            with client.session_transaction() as state:
                tx = state["last_receipt_id"]
            accounts.append((client, user, store, product, tx, label))
        for client, user, store, product, tx, label in accounts:
            for path in (
                "/",
                "/inventory",
                "/inventory/categories",
                "/sale",
                "/transactions",
                "/analytics",
                "/reports",
                "/analytics/export/products.csv",
            ):
                response = client.get(path)
                self.assertEqual(response.status_code, 200)
                for other in accounts:
                    if other[2] != store:
                        text = unescape(response.text)
                        product_name = (
                            "Alpha <Product>" if other[5] == "Alpha" else other[5] + " Product"
                        )
                        store_name = "Alpha <Shop>" if other[5] == "Alpha" else other[5] + " Shop"
                        self.assertNotIn(product_name, text)
                        self.assertNotIn(store_name, text)
                        self.assertNotIn(other[5] + " Category", text)
            other = next(row for row in accounts if row[2] != store)
            self.assertIn(client.get(f"/transactions/{other[4]}/receipt").status_code, (302, 404))
            self.assertEqual(client.get(f"/transactions/{other[4]}/refund").status_code, 404)
            self.assertEqual(
                self.post(
                    f"/transactions/{other[4]}/refund", {"mode": "full", "reason": "Attack"}, client
                ).status_code,
                404,
            )
            self.post("/sale/add", {"product_id": other[3], "quantity": 1}, client)
            with client.session_transaction() as state:
                self.assertNotIn(str(other[3]), state.get("sale_cart", {}))
        self.client.get("/sale")
        self.post("/sale/add", {"product_id": self.product, "quantity": 1})
        login(self.client, "bravo-ui@example.com")
        with self.client.session_transaction() as state:
            self.assertNotIn("sale_cart", state)
        self.assertNotIn("Alpha &lt;Shop&gt;", self.client.get("/").text)
        self.assertTrue(check_database()[0])

    def test_actual_design_color_pairs_meet_text_contrast_goals(self):
        css = Path("static/css/design-system.css").read_text()
        tokens = dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{3,6})\s*;", css))

        def luminance(color):
            value = color.lstrip("#")
            if len(value) == 3:
                value = "".join(char * 2 for char in value)
            channels = [int(value[i : i + 2], 16) / 255 for i in (0, 2, 4)]
            linear = [
                channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4
                for channel in channels
            ]
            return sum(
                channel * weight for channel, weight in zip(linear, (0.2126, 0.7152, 0.0722))
            )

        for foreground, background in [
            ("text-primary", "surface"),
            ("text-secondary", "surface"),
            ("text-muted", "surface"),
            ("surface", "blue"),
            ("green", "green-soft"),
            ("amber", "amber-soft"),
            ("red", "red-soft"),
        ]:
            high, low = sorted(
                (luminance(tokens[foreground]), luminance(tokens[background])), reverse=True
            )
            self.assertGreaterEqual((high + 0.05) / (low + 0.05), 4.5, (foreground, background))
