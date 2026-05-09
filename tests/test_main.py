import io
import json
import sys
import unittest
from unittest import mock

import main


class FakeSeries:
    def __init__(self, values):
        self.values = values

    def dropna(self):
        return self

    @property
    def iloc(self):
        return self

    def __getitem__(self, index):
        return self.values[index]


class FakeHistory:
    def __init__(self, values):
        self.empty = not values
        self.values = values

    def __getitem__(self, key):
        if key != "Close":
            raise KeyError(key)
        return FakeSeries(self.values)


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

    def test_fetch_batch_quotes_returns_successes_and_errors(self):
        def fake_fetch_quote_result(symbol, market):
            if symbol == "不存在":
                raise LookupError("No A-share symbol found for name: 不存在")
            return {
                "symbol": f"resolved-{symbol}",
                "price": 1.23,
                "currency": "CNY",
                "timestamp": "2026-05-09T00:00:00+00:00",
                "source": "yfinance",
            }

        with mock.patch("main.fetch_quote_result", side_effect=fake_fetch_quote_result):
            payload = main.fetch_batch_quotes(["万科A", "不存在"], "CN")

        self.assertEqual(payload["success_count"], 1)
        self.assertEqual(payload["error_count"], 1)
        self.assertEqual(payload["results"][0]["input"], "万科A")
        self.assertEqual(payload["results"][0]["symbol"], "resolved-万科A")
        self.assertEqual(payload["results"][1]["input"], "不存在")
        self.assertEqual(payload["results"][1]["error"]["code"], "SYMBOL_NOT_FOUND")

    def test_get_quote_uses_fast_info(self):
        fake_module = mock.Mock()
        fake_module.Ticker.return_value.fast_info = {
            "lastPrice": 189.84,
            "currency": "USD",
        }

        with mock.patch("main.get_yfinance", return_value=fake_module):
            quote = main.get_quote("AAPL")

        self.assertEqual(quote["symbol"], "AAPL")
        self.assertEqual(quote["price"], 189.84)
        self.assertEqual(quote["currency"], "USD")
        self.assertEqual(quote["source"], "yfinance")

    def test_get_quote_falls_back_to_history(self):
        fake_ticker = mock.Mock()
        fake_ticker.fast_info = {"currency": "HKD"}
        fake_ticker.history.return_value = FakeHistory([412.6])
        fake_module = mock.Mock()
        fake_module.Ticker.return_value = fake_ticker

        with mock.patch("main.get_yfinance", return_value=fake_module):
            quote = main.get_quote("0700.HK")

        self.assertEqual(quote["price"], 412.6)
        self.assertEqual(quote["currency"], "HKD")

    def test_get_quote_raises_when_no_data(self):
        fake_ticker = mock.Mock()
        fake_ticker.fast_info = {}
        fake_ticker.history.return_value = FakeHistory([])
        fake_module = mock.Mock()
        fake_module.Ticker.return_value = fake_ticker

        with mock.patch("main.get_yfinance", return_value=fake_module):
            with self.assertRaisesRegex(LookupError, "No quote found"):
                main.get_quote("XXXX")

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
