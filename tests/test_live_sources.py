import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest


MODULE_PATH = Path(__file__).resolve().parents[1] / "skills" / "get-stock-quotes" / "main.py"
SPEC = importlib.util.spec_from_file_location("main_live", MODULE_PATH)
main = importlib.util.module_from_spec(SPEC)
sys.modules["main_live"] = main
assert SPEC is not None and SPEC.loader is not None
SPEC.loader.exec_module(main)


class LiveSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_get_cache_dir = main.get_cache_dir
        main.get_cache_dir = lambda: self.temp_dir.name

    def tearDown(self):
        main.get_cache_dir = self.original_get_cache_dir
        self.temp_dir.cleanup()

    def assert_valid_quote(self, quote, expected_symbol, expected_currency=None):
        self.assertEqual(quote["symbol"], expected_symbol)
        self.assertIsInstance(quote["price"], float)
        self.assertGreater(quote["price"], 0)
        self.assertIn("source", quote)
        self.assertIn("timestamp", quote)
        if expected_currency is not None:
            self.assertEqual(quote["currency"], expected_currency)

    def test_live_a_share_quote_uses_domestic_source(self):
        quote = main.get_quote("000002.SZ")
        self.assert_valid_quote(quote, "000002.SZ", "CNY")
        self.assertIn(quote["source"], {"eastmoney", "tencent", "sina"})

    def test_live_hk_quote_prefers_domestic_source(self):
        quote = main.get_quote("0700.HK")
        self.assert_valid_quote(quote, "0700.HK", "HKD")
        self.assertIn(quote["source"], {"eastmoney", "sina", "tencent"})

    def test_live_batch_quotes_returns_multiple_real_results(self):
        payload = main.fetch_batch_quotes(["万科A", "0700.HK"], None)
        self.assertEqual(payload["error_count"], 0)
        self.assertEqual(payload["success_count"], 2)
        self.assertEqual(len(payload["results"]), 2)
        symbols = {item["symbol"] for item in payload["results"]}
        self.assertEqual(symbols, {"000002.SZ", "0700.HK"})

    def test_live_quote_cache_is_written_and_reused(self):
        first_quote = main.get_quote("000002.SZ")
        cache_path = Path(main.get_quote_cache_path())
        self.assertTrue(cache_path.exists())

        cache_payload = json.loads(cache_path.read_text(encoding="utf-8"))
        self.assertIn("000002.SZ", cache_payload.get("quotes", {}))

        second_quote = main.get_quote("000002.SZ")
        self.assertEqual(second_quote["source"], first_quote["source"])
        self.assertEqual(second_quote["price"], first_quote["price"])


if __name__ == "__main__":
    unittest.main()
