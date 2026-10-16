"""Exports preserve directory filters, SQL sorting and private contact boundaries."""

import csv
import io
import unittest

import test_customers as fixtures

from services.customers import list_customers, save_customer
from services.purchasing import save_supplier, suppliers


class RetailExportTests(unittest.TestCase):
    setUp = fixtures.CustomerTests.setUp
    tearDown = fixtures.CustomerTests.tearDown
    context = fixtures.CustomerTests.context
    query = fixtures.CustomerTests.query
    post = fixtures.CustomerTests.post

    def test_directory_export_filter_sort_and_formula_parity(self):
        with self.context():
            for name in ("Ä Fictional", "ä Other", "=FORMULA()", "Mixed Case"):
                save_customer({"name": name, "email": "private-contact@example.com"})
                save_supplier({"name": name, "contact": "Private contact"})
        for q in ("Ä", "ä", "FORMULA", "mixed", ""):
            with self.context():
                expected = [
                    row["id"] for row in list_customers({"q": q, "sort": "name"})["customers"]
                ]
                expected_suppliers = [
                    row["id"] for row in suppliers({"q": q, "sort": "name"})["rows"]
                ]
            response = self.client.get(
                "/customers/export.csv", query_string={"q": q, "sort": "name"}
            )
            self.assertEqual(response.status_code, 200)
            rows = list(csv.reader(io.StringIO(response.data.decode("utf-8-sig"))))
            self.assertEqual([int(row[0]) for row in rows[1:]], expected)
            self.assertNotIn(b"private-contact@example.com", response.data)
            if q == "FORMULA":
                self.assertEqual(rows[1][1], "'=FORMULA()")
            response = self.client.get(
                "/suppliers/export.csv", query_string={"q": q, "sort": "name"}
            )
            self.assertEqual(response.status_code, 200)
            rows = list(csv.reader(io.StringIO(response.data.decode("utf-8-sig"))))
            self.assertEqual([int(row[0]) for row in rows[1:]], expected_suppliers)
            self.assertNotIn(b"Private contact", response.data)
