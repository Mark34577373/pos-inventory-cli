"""Actual HTTP demo isolation and semantic checks for the retail workspace."""

import secrets
import unittest

import test_customers as fixtures
from auth_helpers import login
from test_ui import Document

from manage import seed_demo
from services.expenses import create_expense
from services.purchasing import create_order, save_order_line, save_supplier, transition_order
from web_app import app


class RetailSecurityUITests(unittest.TestCase):
    setUp = fixtures.CustomerTests.setUp
    tearDown = fixtures.CustomerTests.tearDown
    context = fixtures.CustomerTests.context
    query = fixtures.CustomerTests.query
    post = fixtures.CustomerTests.post

    def resources(self, index=0):
        from config import business_now

        with self.context(index):
            supplier = save_supplier({"name": f"Fictional supplier {index}"})
            order = create_order(supplier)
            save_order_line(order, self.products[index], 2, "0.50")
            expense = create_expense(
                dict(
                    category="Rent",
                    description=f"Fictional expense {index}",
                    amount="1.00",
                    expense_date=business_now().date().isoformat(),
                ),
                secrets.token_urlsafe(24),
            )
        return supplier, order, expense

    def test_semantic_new_pages_controls_csrf_and_navigation(self):
        supplier, order, expense = self.resources()
        paths = [
            "/customers",
            "/customers/new",
            f"/customers/{self.ids[0]}",
            f"/customers/{self.ids[0]}/edit",
            "/suppliers",
            "/suppliers/new",
            f"/suppliers/{supplier}",
            f"/suppliers/{supplier}/edit",
            "/purchases",
            "/purchases/new",
            f"/purchases/{order}",
            f"/inventory/{self.products[0]}/cost",
            "/expenses",
            "/expenses/new",
            f"/expenses/{expense}",
            f"/expenses/{expense}/correct",
            "/financials",
            "/sale",
        ]
        self.client.get("/sale")
        self.post("/sale/add", dict(product_id=self.products[0], quantity=1))
        for path in paths:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                doc = Document(response.text)
                self.assertEqual(doc.errors, [])
                self.assertEqual(doc.stack, [])
                self.assertEqual(len(doc.find("h1")), 1)
                self.assertEqual(len(doc.find("main")), 1)
                ids = [node.attrs["id"] for node in doc.nodes if "id" in node.attrs]
                self.assertEqual(len(ids), len(set(ids)))
                labels = {node.attrs.get("for") for node in doc.find("label")}
                for node in doc.nodes:
                    if (
                        node.tag in ("input", "select", "textarea")
                        and node.attrs.get("type") != "hidden"
                    ):
                        self.assertTrue(
                            node.attrs.get("id") in labels
                            or node.inside("label")
                            or node.attrs.get("aria-label"),
                            node.attrs,
                        )
                for form in doc.find("form"):
                    self.assertFalse(form.inside("form"))
                    if form.attrs.get("method", "get").lower() == "post":
                        self.assertIn(
                            "csrf_token",
                            [
                                node.attrs.get("name")
                                for node in doc.find("input")
                                if self.belongs(node, form)
                            ],
                        )
                for heading in doc.find("th"):
                    self.assertEqual(heading.attrs.get("scope"), "col")
                self.assertIn("no-store", response.headers["Cache-Control"])
                if path != "/sale":
                    self.assertEqual(
                        sum(
                            "active" in node.attrs.get("class", "").split()
                            for node in doc.find("a")
                            if "nav-link" in node.attrs.get("class", "").split()
                        ),
                        1,
                    )
        with self.context():
            transition_order(order, "order")
        self.assertIn(b"Confirm receiving", self.client.get(f"/purchases/{order}").data)

    @staticmethod
    def belongs(node, parent):
        while node.parent:
            node = node.parent
            if node is parent:
                return True
        return False

    def test_each_of_three_accounts_can_only_open_own_resources(self):
        records = [self.resources(index) for index in range(3)]
        for index, name in enumerate(("alpha", "beta", "gamma")):
            login(self.client, f"{name}@example.com")
            for target, (supplier, order, expense) in enumerate(records):
                for path in (
                    f"/customers/{self.ids[target]}",
                    f"/suppliers/{supplier}",
                    f"/purchases/{order}",
                    f"/expenses/{expense}",
                    f"/inventory/{self.products[target]}/cost",
                ):
                    self.assertEqual(
                        self.client.get(path).status_code,
                        200 if target == index else 404,
                        (index, target, path),
                    )
            with self.client.session_transaction() as state:
                self.assertNotIn("sale_customer_id", state)
                self.assertNotIn("sale_discount", state)

    def test_public_demo_new_routes_are_isolated_and_read_only(self):
        supplier, order, expense = self.resources()
        seed_demo(allow_existing=True)
        demo = app.test_client()
        demo.get("/login")
        with demo.session_transaction() as state:
            token = state["csrf_token"]
        self.assertEqual(demo.post("/demo", data={"csrf_token": token}).status_code, 302)
        before = self.query("SELECT * FROM products")
        for path in (
            "/customers",
            "/suppliers",
            "/purchases",
            "/expenses",
            "/financials",
            "/customers/new",
            "/suppliers/new",
            "/purchases/new",
            "/expenses/new",
        ):
            response = demo.get(path)
            self.assertEqual(response.status_code, 200, path)
            document = Document(response.text)
            actions = [
                form.attrs.get("action", path)
                for form in document.find("form")
                if form.attrs.get("method", "get").lower() == "post"
            ]
            self.assertEqual(actions, ["/logout"], path)
            self.assertNotIn(b"Fictional supplier 0", response.data)
        for path in (
            f"/customers/{self.ids[0]}",
            f"/suppliers/{supplier}",
            f"/purchases/{order}",
            f"/expenses/{expense}",
        ):
            self.assertEqual(demo.get(path).status_code, 404)
        with demo.session_transaction() as state:
            token = state["csrf_token"]
        for path in (
            "/customers/new",
            "/suppliers/new",
            "/purchases/new",
            "/expenses/new",
            "/sale/customer",
            "/sale/discount",
            f"/purchases/{order}/receive",
            f"/expenses/{expense}/reverse",
            f"/inventory/{self.products[0]}/cost",
        ):
            self.assertEqual(demo.post(path, data={"csrf_token": token}).status_code, 403)
        self.assertEqual(self.query("SELECT * FROM products"), before)
