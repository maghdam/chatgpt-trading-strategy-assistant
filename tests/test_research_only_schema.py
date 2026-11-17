"""Offline contract guard for the private research-only Action staging schema."""
from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "gpt-schema-research-only.json"

# This is a deliberately small allowlist, not an HTTP-method-based heuristic:
# read-only backend operations currently use POST.
ALLOWED_OPERATIONS = {
    ("/analyze", "post"): "analyzeSMCResearch",
    ("/fetch-data", "post"): "fetchOHLCResearch",
}
FORBIDDEN_PATHS = {
    "/place-order",
    "/placeOrder",
    "/journal-entry",
    "/open-positions",
    "/pending-orders",
    "/fundamental-data",
}


class PrivateResearchContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.document = json.loads(SCHEMA.read_text(encoding="utf-8"))

    def test_staging_schema_has_exact_two_read_only_operations(self):
        self.assertEqual(self.document["openapi"], "3.1.0")
        actual = {
            (path, method.lower()): details.get("operationId")
            for path, methods in self.document["paths"].items()
            for method, details in methods.items()
        }
        self.assertEqual(actual, ALLOWED_OPERATIONS)
        for path, methods in self.document["paths"].items():
            for method, details in methods.items():
                self.assertTrue(details["x-research-only"], f"{method.upper()} {path}")

    def test_staging_schema_omits_account_journal_and_execution_access(self):
        paths = set(self.document["paths"])
        self.assertTrue(paths.isdisjoint(FORBIDDEN_PATHS))
        text = SCHEMA.read_text(encoding="utf-8")
        for forbidden in ("placeOrder", "postJournalEntry", "getOpenPositions", "getPendingOrders"):
            self.assertNotIn(forbidden, text)

    def test_no_live_service_host_is_bundled(self):
        self.assertEqual(self.document["servers"][0]["url"], "https://example.invalid")
        self.assertNotIn("onrender.com", SCHEMA.read_text(encoding="utf-8"))

    def test_read_only_requests_require_symbol_and_reject_extra_fields(self):
        for operation in ALLOWED_OPERATIONS:
            path, method = operation
            body = self.document["paths"][path][method]["requestBody"]
            self.assertTrue(body["required"])
            schema = body["content"]["application/json"]["schema"]
            self.assertEqual(schema["required"], ["symbol"])
            self.assertFalse(schema["additionalProperties"])
            self.assertNotIn("account_id", schema["properties"])
            self.assertNotIn("access_token", schema["properties"])
            self.assertNotIn("volume", schema["properties"])


if __name__ == "__main__":
    unittest.main()
