import io
import importlib.util
import json
from pathlib import Path
import sys
import time
import unittest
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "skills" / "get-stock-quotes" / "main.py"
SPEC = importlib.util.spec_from_file_location("main", MODULE_PATH)
main = importlib.util.module_from_spec(SPEC)
sys.modules["main"] = main
assert SPEC is not None and SPEC.loader is not None
SPEC.loader.exec_module(main)


class MainTests(unittest.TestCase):
    def test_parse_input_from_args(self):
        symbol, market = main.parse_input(["--symbol", "700", "--market", "HK"])
        self.assertEqual(symbol, "700")
        self.assertEqual(market, "HK")

    def test_parse_request_from_symbols_args(self):
        symbols, market = main.parse_request(["--symbols", "万科A,闻泰科技", "--market", "CN"])
        self.assertEqual(symbols, ["万科A", "闻泰科技"])
        self.assertEqual(market, "CN")

    def test_parse_input_from_json_stdin(self):
        symbol, market = main.parse_input(argv=[], stdin_text='{"symbol":"600519","market":"CN"}')
        self.assertEqual(symbol, "600519")
        self.assertEqual(market, "CN")

    def test_parse_request_from_json_symbol_list(self):
        symbols, market = main.parse_request(argv=[], stdin_text='{"symbols":["万科A","北京君正"],"market":"CN"}')
        self.assertEqual(symbols, ["万科A", "北京君正"])
        self.assertEqual(market, "CN")

    def test_normalize_symbol_auto_cn_and_hk(self):
        self.assertEqual(main.normalize_symbol("600519"), "600519.SS")
        self.assertEqual(main.normalize_symbol("000001"), "000001.SZ")
        self.assertEqual(main.normalize_symbol("700"), "0700.HK")
        self.assertEqual(main.normalize_symbol("00700"), "0700.HK")

    def test_normalize_symbol_prefixed_and_suffixed(self):
        self.assertEqual(main.normalize_symbol("sh600519"), "600519.SS")
        self.assertEqual(main.normalize_symbol("600519.SH"), "600519.SS")
        self.assertEqual(main.normalize_symbol("hk700"), "0700.HK")

    def test_normalize_symbol_rejects_unknown_cn_exchange(self):
        with self.assertRaisesRegex(ValueError, "Cannot infer A-share exchange"):
            main.normalize_symbol("123456", market="CN")

    def test_normalize_a_share_name_handles_spaces_and_fullwidth(self):
        self.assertEqual(main.normalize_a_share_name("万 科Ａ"), "万科A")

    def test_resolve_a_share_name_from_cached_entries(self):
        with mock.patch(
            "main.get_a_share_entries",
            return_value=[{"code": "000002", "name": "万科A"}],
        ):
            self.assertEqual(main.resolve_a_share_name("万科A"), "000002")

    def test_resolve_a_share_name_uses_search_fallback(self):
        with mock.patch("main.get_a_share_entries", return_value=[]), mock.patch(
            "main.search_a_share_candidates",
            return_value=[{"code": "600745", "name": "闻泰科技"}],
        ):
            self.assertEqual(main.resolve_a_share_name("闻泰科技"), "600745")

    def test_resolve_a_share_name_reports_ambiguity(self):
        with mock.patch(
            "main.get_a_share_entries",
            return_value=[
                {"code": "000001", "name": "示例股份"},
                {"code": "000002", "name": "示例股份"},
            ],
        ):
            with self.assertRaises(main.AmbiguousSymbolError):
                main.resolve_a_share_name("示例股份")

    def test_resolve_symbol_maps_chinese_name_to_cn_code(self):
        with mock.patch("main.resolve_a_share_name", return_value="300223"):
            self.assertEqual(main.resolve_symbol("北京君正", None), ("300223", "CN"))

    def test_fetch_quotes_uses_cache_before_network(self):
        cached_quote = {
            "symbol": "000002.SZ",
            "price": 4.0,
            "currency": "CNY",
            "timestamp": "2026-05-09T00:00:00+00:00",
            "source": "eastmoney",
        }
        cache_payload = {
            "000002.SZ": {
                "fetched_at": time.time(),
                "quote": cached_quote,
            }
        }

        with mock.patch("main.load_quote_cache", return_value=cache_payload), mock.patch(
            "main.fetch_quote_from_domestic_sources"
        ) as domestic_mock, mock.patch("main.get_yfinance_batch_quotes") as yahoo_batch_mock:
            quotes, errors = main.fetch_quotes(["000002.SZ"])

        self.assertEqual(quotes["000002.SZ"], cached_quote)
        self.assertEqual(errors, {})
        domestic_mock.assert_not_called()
        yahoo_batch_mock.assert_not_called()

    def test_fetch_quotes_prefers_domestic_source_for_cn_symbol(self):
        domestic_quote = {
            "symbol": "000002.SZ",
            "price": 4.0,
            "currency": "CNY",
            "timestamp": "2026-05-09T00:00:00+00:00",
            "source": "eastmoney",
        }

        with mock.patch("main.load_quote_cache", return_value={}), mock.patch(
            "main.fetch_quote_from_domestic_sources", return_value=domestic_quote
        ) as domestic_mock, mock.patch("main.get_yfinance_batch_quotes") as yahoo_batch_mock, mock.patch(
            "main.save_quote_cache"
        ) as save_cache_mock:
            quotes, errors = main.fetch_quotes(["000002.SZ"])

        self.assertEqual(quotes["000002.SZ"]["source"], "eastmoney")
        self.assertEqual(errors, {})
        domestic_mock.assert_called_once_with("000002.SZ")
        yahoo_batch_mock.assert_not_called()
        save_cache_mock.assert_called_once()

    def test_fetch_quotes_falls_back_to_yahoo_when_domestic_fails(self):
        yahoo_quote = {
            "symbol": "000002.SZ",
            "price": 4.01,
            "currency": "CNY",
            "timestamp": "2026-05-09T00:00:00+00:00",
            "source": "yfinance",
        }

        with mock.patch("main.load_quote_cache", return_value={}), mock.patch(
            "main.fetch_quote_from_domestic_sources", side_effect=RuntimeError("domestic down")
        ), mock.patch("main.get_yfinance_batch_quotes", return_value={"000002.SZ": yahoo_quote}) as yahoo_batch_mock, mock.patch(
            "main.save_quote_cache"
        ):
            quotes, errors = main.fetch_quotes(["000002.SZ"])

        self.assertEqual(quotes["000002.SZ"]["source"], "yfinance")
        self.assertEqual(errors, {})
        yahoo_batch_mock.assert_called_once_with(["000002.SZ"])

    def test_prune_quote_cache_removes_expired_and_limits_size(self):
        now = time.time()
        quotes = {
            "A": {"fetched_at": now - 10, "quote": {"symbol": "A"}},
            "B": {"fetched_at": now - 20, "quote": {"symbol": "B"}},
            "C": {"fetched_at": now - main.QUOTE_CACHE_TTL_SECONDS - 1, "quote": {"symbol": "C"}},
        }

        with mock.patch.object(main, "QUOTE_CACHE_MAX_ENTRIES", 1):
            pruned = main.prune_quote_cache(quotes)

        self.assertEqual(list(pruned.keys()), ["A"])

    def test_fetch_quote_from_domestic_sources_orders_hk_sources(self):
        calls = []

        def fake_eastmoney(symbol):
            calls.append("eastmoney")
            raise LookupError("eastmoney down")

        def fake_sina(symbol):
            calls.append("sina")
            return {"symbol": symbol, "price": 1.0, "currency": "HKD", "timestamp": "t", "source": "sina"}

        def fake_tencent(symbol):
            calls.append("tencent")
            raise AssertionError("tencent should not be called")

        with mock.patch("main.fetch_eastmoney_quote", side_effect=fake_eastmoney), mock.patch(
            "main.fetch_sina_quote", side_effect=fake_sina
        ), mock.patch("main.fetch_tencent_quote", side_effect=fake_tencent):
            quote = main.fetch_quote_from_domestic_sources("0700.HK")

        self.assertEqual(quote["source"], "sina")
        self.assertEqual(calls, ["eastmoney", "sina"])

    def test_suppress_external_noise_hides_stderr_but_restores_it(self):
        stderr_buffer = io.StringIO()
        original_stderr = sys.stderr

        with mock.patch("sys.stderr", stderr_buffer):
            with main.suppress_external_noise():
                print("third-party-noise", file=sys.stderr)
            print("restored", file=sys.stderr)

        sys.stderr = original_stderr
        self.assertEqual(stderr_buffer.getvalue(), "restored\n")

    def test_main_emits_structured_error_for_invalid_input(self):
        stdout = io.StringIO()
        with mock.patch("sys.argv", ["main.py"]), mock.patch("sys.stdout", stdout), mock.patch("sys.stdin", io.StringIO("")):
            with self.assertRaises(SystemExit) as exc:
                main.main()

        self.assertEqual(exc.exception.code, 2)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["error"]["code"], "INVALID_INPUT")

    def test_main_emits_ambiguous_symbol_error(self):
        stdout = io.StringIO()
        with mock.patch("sys.argv", ["main.py"]), mock.patch("sys.stdout", stdout), mock.patch(
            "sys.stdin", io.StringIO('{"symbol":"示例股份"}')
        ), mock.patch("main.resolve_symbol", side_effect=main.AmbiguousSymbolError("Multiple symbols found")):
            with self.assertRaises(SystemExit) as exc:
                main.main()

        self.assertEqual(exc.exception.code, 4)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["error"]["code"], "AMBIGUOUS_SYMBOL")

    def test_main_emits_batch_results(self):
        stdout = io.StringIO()
        with mock.patch("sys.argv", ["main.py", "--symbols", "万科A,闻泰科技"]), mock.patch(
            "sys.stdout", stdout
        ), mock.patch(
            "main.fetch_batch_quotes",
            return_value={
                "results": [
                    {"input": "万科A", "symbol": "000002.SZ", "price": 4.0, "currency": "CNY", "timestamp": "t1", "source": "yfinance"},
                    {"input": "闻泰科技", "symbol": "600745.SS", "price": 24.1, "currency": "CNY", "timestamp": "t2", "source": "yfinance"},
                ],
                "success_count": 2,
                "error_count": 0,
            },
        ):
            with self.assertRaises(SystemExit) as exc:
                main.main()

        self.assertEqual(exc.exception.code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["success_count"], 2)
        self.assertEqual(len(payload["results"]), 2)


if __name__ == "__main__":
    unittest.main()
