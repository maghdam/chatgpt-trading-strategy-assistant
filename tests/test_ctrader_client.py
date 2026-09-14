import os
import unittest
from unittest.mock import patch

os.environ.setdefault("CTRADER_ACCOUNT_ID", "123")

import ctrader_client as ctrader
from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOAErrorRes,
    ProtoOASymbolsListRes,
)


class SymbolResponseTests(unittest.TestCase):
    def setUp(self):
        ctrader._reset_symbol_state()

    def tearDown(self):
        ctrader._reset_symbol_state()

    def test_valid_symbol_list_populates_catalogue(self):
        payload = ProtoOASymbolsListRes(ctidTraderAccountId=123)
        symbol = payload.symbol.add()
        symbol.symbolId = 42
        symbol.symbolName = "BCHUSD"
        symbol.enabled = True

        with patch.object(ctrader.Protobuf, "extract", return_value=payload):
            result = ctrader.symbols_response_cb(object())

        self.assertIs(result, payload)
        self.assertEqual(ctrader.symbol_name_to_id["BCHUSD"], 42)
        self.assertEqual(ctrader.symbol_map[42], "BCHUSD")
        self.assertEqual(ctrader.symbol_digits_map[42], 5)
        self.assertEqual(
            ctrader.get_symbol_status(),
            {
                "ready": True,
                "symbols_loaded": 1,
                "error": None,
            },
        )
        ctrader.wait_for_symbols(timeout=0)

    def test_error_payload_is_consumed_and_reported(self):
        payload = ProtoOAErrorRes(
            ctidTraderAccountId=123,
            errorCode="ACCOUNT_NOT_AUTHORIZED",
        )

        with patch.object(ctrader.Protobuf, "extract", return_value=payload):
            result = ctrader.symbols_response_cb(object())

        self.assertIsNone(result)
        status = ctrader.get_symbol_status()
        self.assertFalse(status["ready"])
        self.assertEqual(status["symbols_loaded"], 0)
        self.assertIn("ProtoOAErrorRes", status["error"])
        self.assertIn("ACCOUNT_NOT_AUTHORIZED", status["error"])

        with self.assertRaisesRegex(
            ctrader.CTraderUnavailableError,
            "ACCOUNT_NOT_AUTHORIZED",
        ):
            ctrader.wait_for_symbols(timeout=0)

    def test_empty_symbol_list_is_reported_as_unavailable(self):
        payload = ProtoOASymbolsListRes(ctidTraderAccountId=123)

        with patch.object(ctrader.Protobuf, "extract", return_value=payload):
            result = ctrader.symbols_response_cb(object())

        self.assertIsNone(result)
        self.assertIn(
            "empty symbol catalogue",
            ctrader.get_symbol_status()["error"],
        )


if __name__ == "__main__":
    unittest.main()
