import argparse
import contextlib
import json
import logging
import os
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple


CN_SH_PREFIXES = ("600", "601", "603", "605", "688")
CN_SZ_PREFIXES = ("000", "001", "002", "003", "300", "301")
CN_BJ_PREFIXES = (
    "430",
    "440",
    "830",
    "831",
    "832",
    "833",
    "834",
    "835",
    "836",
    "837",
    "838",
    "839",
    "870",
    "871",
    "872",
    "873",
    "874",
    "875",
    "876",
    "877",
    "878",
    "879",
    "920",
)
MARKET_ALIASES = {
    "AUTO": None,
    "": None,
    "CN": "CN",
    "A": "CN",
    "A-SHARE": "CN",
    "ASHARE": "CN",
    "HK": "HK",
    "HKG": "HK",
    "US": "US",
}
SUFFIX_ALIASES = {
    "SH": "SS",
    "SS": "SS",
    "SZ": "SZ",
    "BJ": "BJ",
    "HK": "HK",
}
A_SHARE_CACHE_TTL_SECONDS = 24 * 60 * 60
QUOTE_CACHE_TTL_SECONDS = 30
QUOTE_CACHE_MAX_ENTRIES = 256
A_SHARE_UNIVERSE_URL = (
    "https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=10000&po=1&np=1&fltt=2&invt=2"
    "&fid=f3&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23&fields=f12,f14"
)
A_SHARE_SEARCH_URL = (
    "https://searchapi.eastmoney.com/api/suggest/get?type=14"
    "&token=D43BF722C8E33BDC906FB84D85E326E8&input={query}"
)
DEFAULT_HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json,text/plain,*/*",
}
TENCENT_HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://gu.qq.com/",
}
SINA_HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://finance.sina.com.cn/",
}


def get_yfinance():
    try:
        import yfinance as yf
    except ImportError as exc:
        raise RuntimeError("yfinance is not installed") from exc
    return yf


class AmbiguousSymbolError(LookupError):
    pass


@contextlib.contextmanager
def suppress_external_noise():
    devnull_file = open(os.devnull, "w")
    devnull_fd = devnull_file.fileno()
    saved_stderr_fd = os.dup(2)
    saved_stderr = sys.stderr
    yahoo_logger = logging.getLogger("yfinance")
    yahoo_level = yahoo_logger.level
    yahoo_disabled = yahoo_logger.disabled

    try:
        yahoo_logger.disabled = True
        yahoo_logger.setLevel(logging.CRITICAL)
        sys.stderr.flush()
        sys.stderr = devnull_file
        os.dup2(devnull_fd, 2)
        yield
    finally:
        sys.stderr.flush()
        os.dup2(saved_stderr_fd, 2)
        sys.stderr = saved_stderr
        os.close(saved_stderr_fd)
        devnull_file.close()
        yahoo_logger.disabled = yahoo_disabled
        yahoo_logger.setLevel(yahoo_level)


def normalize_market(market: Optional[str]) -> Optional[str]:
    if market is None:
        return None
    normalized = market.strip().upper()
    if normalized not in MARKET_ALIASES:
        raise ValueError(f"Unsupported market: {market}")
    return MARKET_ALIASES[normalized]


def contains_chinese(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)


def normalize_a_share_name(name: str) -> str:
    normalized = unicodedata.normalize("NFKC", name)
    normalized = re.sub(r"\s+", "", normalized)
    return normalized.upper()


def get_cache_dir() -> str:
    skill_root = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(skill_root, ".cache")


def get_cache_path() -> str:
    return os.path.join(get_cache_dir(), "a_share_symbols.json")


def get_quote_cache_path() -> str:
    return os.path.join(get_cache_dir(), "quotes.json")


def get_quote_test_cache_path() -> str:
    return os.path.join(get_cache_dir(), "quotes.test.json")


def fetch_json(url: str) -> dict:
    request = urllib.request.Request(url, headers=DEFAULT_HTTP_HEADERS)
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_text(url: str, headers: Optional[dict] = None, encoding: str = "utf-8") -> str:
    request = urllib.request.Request(url, headers=headers or DEFAULT_HTTP_HEADERS)
    with urllib.request.urlopen(request, timeout=15) as response:
        return response.read().decode(encoding, errors="ignore")


def load_json_file(path: str) -> Optional[dict]:
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def save_json_file(path: str, payload: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp_path = f"{path}.tmp"
    with open(temp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
    os.replace(temp_path, path)


def load_a_share_cache() -> Optional[dict]:
    payload = load_json_file(get_cache_path())
    if payload is None:
        return None

    if not isinstance(payload, dict) or not isinstance(payload.get("entries"), list):
        return None
    return payload


def save_a_share_cache(entries: list) -> None:
    save_json_file(get_cache_path(), {"fetched_at": time.time(), "entries": entries})


def load_quote_cache() -> dict:
    payload = load_json_file(get_quote_cache_path())
    quotes = payload.get("quotes") if isinstance(payload, dict) else None
    return quotes if isinstance(quotes, dict) else {}


def save_quote_cache(quotes: dict) -> None:
    save_json_file(get_quote_cache_path(), {"quotes": quotes})


def prune_quote_cache(quotes: dict) -> dict:
    valid_entries = []
    now = time.time()
    for symbol, cached in quotes.items():
        if not isinstance(cached, dict):
            continue
        fetched_at = cached.get("fetched_at")
        quote = cached.get("quote")
        if not isinstance(fetched_at, (int, float)) or not isinstance(quote, dict):
            continue
        if now - fetched_at > QUOTE_CACHE_TTL_SECONDS:
            continue
        valid_entries.append((symbol, cached))

    valid_entries.sort(key=lambda item: item[1]["fetched_at"], reverse=True)
    return dict(valid_entries[:QUOTE_CACHE_MAX_ENTRIES])


def get_cached_quote(quotes: dict, symbol: str) -> Optional[dict]:
    cached = quotes.get(symbol)
    if not isinstance(cached, dict):
        return None
    fetched_at = cached.get("fetched_at")
    quote = cached.get("quote")
    if not isinstance(fetched_at, (int, float)) or not isinstance(quote, dict):
        return None
    if time.time() - fetched_at > QUOTE_CACHE_TTL_SECONDS:
        return None
    return quote


def set_cached_quote(quotes: dict, quote: dict) -> None:
    quotes[quote["symbol"]] = {"fetched_at": time.time(), "quote": quote}


def fetch_a_share_entries() -> list:
    payload = fetch_json(A_SHARE_UNIVERSE_URL)
    rows = payload.get("data", {}).get("diff") or []
    entries = []
    for row in rows:
        code = str(row.get("f12", "")).strip()
        name = str(row.get("f14", "")).strip()
        if re.fullmatch(r"\d{6}", code) and name:
            entries.append({"code": code, "name": name})
    if not entries:
        raise RuntimeError("Failed to load A-share symbol list")
    return entries


def get_a_share_entries() -> list:
    payload = load_a_share_cache()
    if payload:
        fetched_at = payload.get("fetched_at")
        if isinstance(fetched_at, (int, float)) and time.time() - fetched_at <= A_SHARE_CACHE_TTL_SECONDS:
            return payload["entries"]

    try:
        entries = fetch_a_share_entries()
    except Exception:
        if payload:
            return payload["entries"]
        raise

    save_a_share_cache(entries)
    return entries


def search_a_share_candidates(name: str) -> list:
    query = urllib.parse.quote(name)
    payload = fetch_json(A_SHARE_SEARCH_URL.format(query=query))
    rows = payload.get("QuotationCodeTable", {}).get("Data") or []
    candidates = []
    for row in rows:
        if row.get("Classify") != "AStock":
            continue
        code = str(row.get("Code", "")).strip()
        resolved_name = str(row.get("Name", "")).strip()
        if re.fullmatch(r"\d{6}", code) and resolved_name:
            candidates.append({"code": code, "name": resolved_name})
    return candidates


def format_candidates(candidates: list) -> str:
    return ", ".join(f"{candidate['name']}({candidate['code']})" for candidate in candidates)


def resolve_a_share_name(name: str) -> str:
    normalized_name = normalize_a_share_name(name)
    exact_matches = [
        entry for entry in get_a_share_entries() if normalize_a_share_name(entry["name"]) == normalized_name
    ]
    if len(exact_matches) == 1:
        return exact_matches[0]["code"]
    if len(exact_matches) > 1:
        raise AmbiguousSymbolError(
            f"Multiple A-share symbols found for name: {name} ({format_candidates(exact_matches)})"
        )

    candidates = search_a_share_candidates(name)
    exact_candidates = [
        candidate for candidate in candidates if normalize_a_share_name(candidate["name"]) == normalized_name
    ]
    if len(exact_candidates) == 1:
        return exact_candidates[0]["code"]
    if len(exact_candidates) > 1:
        raise AmbiguousSymbolError(
            f"Multiple A-share symbols found for name: {name} ({format_candidates(exact_candidates)})"
        )
    if len(candidates) == 1:
        return candidates[0]["code"]
    if candidates:
        raise AmbiguousSymbolError(
            f"Multiple A-share symbols found for name: {name} ({format_candidates(candidates[:5])})"
        )
    raise LookupError(f"No A-share symbol found for name: {name}")


def resolve_symbol(symbol: str, market: Optional[str]) -> Tuple[str, Optional[str]]:
    if contains_chinese(symbol) and market != "HK" and market != "US":
        return resolve_a_share_name(symbol), market or "CN"
    return symbol, market


def split_symbol_values(raw: str) -> List[str]:
    return [item.strip() for item in re.split(r"[,，;；\n]+", raw) if item.strip()]


def infer_cn_exchange(symbol: str) -> Optional[str]:
    if symbol.startswith(CN_SH_PREFIXES):
        return "SS"
    if symbol.startswith(CN_SZ_PREFIXES):
        return "SZ"
    if symbol.startswith(CN_BJ_PREFIXES):
        return "BJ"
    return None


def normalize_hk_code(symbol: str) -> str:
    numeric = str(int(symbol))
    return f"{numeric.zfill(4)}.HK"


def normalize_symbol(symbol: str, market: Optional[str] = None) -> str:
    cleaned = symbol.strip().upper()
    if not cleaned:
        raise ValueError("Missing required input: symbol")

    normalized_market = normalize_market(market)

    prefixed_match = re.fullmatch(r"(SH|SZ|BJ|HK)(\d+)", cleaned)
    if prefixed_match:
        prefix, digits = prefixed_match.groups()
        cleaned = f"{digits}.{SUFFIX_ALIASES[prefix]}"

    suffixed_match = re.fullmatch(r"([0-9]+)\.(SH|SS|SZ|BJ|HK)", cleaned)
    if suffixed_match:
        digits, suffix = suffixed_match.groups()
        suffix = SUFFIX_ALIASES[suffix]
        if suffix == "HK":
            return normalize_hk_code(digits)
        return f"{digits}.{suffix}"

    if normalized_market == "HK" and cleaned.isdigit():
        return normalize_hk_code(cleaned)

    if normalized_market == "CN" and cleaned.isdigit():
        if len(cleaned) != 6:
            raise ValueError("A-share symbols must be 6 digits")
        exchange = infer_cn_exchange(cleaned)
        if exchange is None:
            raise ValueError(f"Cannot infer A-share exchange for symbol: {cleaned}")
        return f"{cleaned}.{exchange}"

    if normalized_market == "US":
        return cleaned

    if cleaned.isdigit():
        if len(cleaned) == 6:
            exchange = infer_cn_exchange(cleaned)
            if exchange:
                return f"{cleaned}.{exchange}"
        if 1 <= len(cleaned) <= 5:
            return normalize_hk_code(cleaned)

    return cleaned


def parse_input(argv: Optional[List[str]] = None, stdin_text: Optional[str] = None) -> Tuple[str, Optional[str]]:
    symbols, market = parse_request(argv, stdin_text)
    if len(symbols) != 1:
        raise ValueError("Expected exactly one symbol")
    return symbols[0], market


def parse_request(argv: Optional[List[str]] = None, stdin_text: Optional[str] = None) -> Tuple[List[str], Optional[str]]:
    parser = argparse.ArgumentParser(description="Fetch stock quotes by ticker symbol")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--symbol", help="Ticker symbol such as AAPL or 600519.SS")
    group.add_argument("--symbols", help="Comma or newline separated symbols or names")
    parser.add_argument("--market", help="Optional market hint such as US, CN, or HK")
    args = parser.parse_args(argv)
    if args.symbol:
        return [args.symbol.strip()], normalize_market(args.market)
    if args.symbols:
        symbols = split_symbol_values(args.symbols)
        if not symbols:
            raise ValueError("Missing required input: symbol")
        return symbols, normalize_market(args.market)

    raw = stdin_text if stdin_text is not None else sys.stdin.read()
    raw = raw.strip()
    if not raw:
        raise ValueError("Missing required input: symbol")

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        symbols = split_symbol_values(raw)
        if not symbols:
            raise ValueError("Missing required input: symbol")
        return symbols, None

    if isinstance(payload, str):
        symbols = split_symbol_values(payload)
        if not symbols:
            raise ValueError("Missing required input: symbol")
        return symbols, None
    if isinstance(payload, list) and all(isinstance(item, str) for item in payload):
        symbols = [item.strip() for item in payload if item.strip()]
        if not symbols:
            raise ValueError("Missing required input: symbol")
        return symbols, None
    if isinstance(payload, dict):
        market = normalize_market(payload.get("market"))
        if isinstance(payload.get("symbol"), str):
            return [payload["symbol"].strip()], market
        if isinstance(payload.get("symbols"), list) and all(isinstance(item, str) for item in payload["symbols"]):
            symbols = [item.strip() for item in payload["symbols"] if item.strip()]
            if not symbols:
                raise ValueError("Missing required input: symbol")
            return symbols, market
    raise ValueError("Missing required input: symbol")


def format_error(exc: Exception) -> dict:
    if isinstance(exc, AmbiguousSymbolError):
        return {"code": "AMBIGUOUS_SYMBOL", "message": str(exc)}
    if isinstance(exc, ValueError):
        return {"code": "INVALID_INPUT", "message": str(exc)}
    if isinstance(exc, LookupError):
        return {"code": "SYMBOL_NOT_FOUND", "message": str(exc)}
    return {"code": "UPSTREAM_ERROR", "message": str(exc)}


def fetch_quote_result(symbol: str, market: Optional[str]) -> dict:
    resolved_symbol, resolved_market = resolve_symbol(symbol, market)
    normalized_symbol = normalize_symbol(resolved_symbol, resolved_market)
    return get_quote(normalized_symbol)


def infer_currency_from_symbol(symbol: str) -> str:
    if symbol.endswith((".SS", ".SZ", ".BJ")):
        return "CNY"
    if symbol.endswith(".HK"):
        return "HKD"
    return "UNKNOWN"


def get_eastmoney_price_scale(symbol: str) -> int:
    if symbol.endswith(".HK"):
        return 1000
    return 100


def extract_latest_close(history, symbol: str) -> float:
    close_series = history[(symbol, "Close")].dropna()
    if close_series.empty:
        raise LookupError(f"No quote found for symbol: {symbol}")
    return float(close_series.iloc[-1])


def make_quote(symbol: str, price: float, currency: Optional[str], source: str) -> dict:
    return {
        "symbol": symbol,
        "price": float(price),
        "currency": currency or infer_currency_from_symbol(symbol),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": source,
    }


def is_cn_symbol(symbol: str) -> bool:
    return symbol.endswith((".SS", ".SZ", ".BJ"))


def is_hk_symbol(symbol: str) -> bool:
    return symbol.endswith(".HK")


def prefers_domestic_sources(symbol: str) -> bool:
    return is_cn_symbol(symbol) or is_hk_symbol(symbol)


def get_cn_provider_prefix(symbol: str) -> Optional[str]:
    if symbol.endswith(".SS"):
        return "sh"
    if symbol.endswith(".SZ"):
        return "sz"
    if symbol.endswith(".BJ"):
        return "bj"
    return None


def get_hk_provider_code(symbol: str) -> Optional[str]:
    if not is_hk_symbol(symbol):
        return None
    return symbol.split(".", 1)[0].zfill(5)


def get_eastmoney_secids(symbol: str) -> List[str]:
    if symbol.endswith(".SS"):
        return [f"1.{symbol[:6]}"]
    if symbol.endswith(".SZ"):
        return [f"0.{symbol[:6]}"]
    if symbol.endswith(".HK"):
        hk_code = get_hk_provider_code(symbol)
        return [f"116.{hk_code}", f"128.{hk_code}"] if hk_code is not None else []
    return []


def get_tencent_symbol(symbol: str) -> Optional[str]:
    prefix = get_cn_provider_prefix(symbol)
    if prefix is not None:
        return f"{prefix}{symbol[:6]}"
    if symbol.endswith(".HK"):
        hk_code = get_hk_provider_code(symbol)
        return f"hk{hk_code}" if hk_code is not None else None
    return None


def get_sina_symbol(symbol: str) -> Optional[str]:
    prefix = get_cn_provider_prefix(symbol)
    if prefix in {"sh", "sz"}:
        return f"{prefix}{symbol[:6]}"
    if symbol.endswith(".HK"):
        hk_code = get_hk_provider_code(symbol)
        return f"rt_hk{hk_code}" if hk_code is not None else None
    return None


def fetch_eastmoney_quote(symbol: str) -> dict:
    secids = get_eastmoney_secids(symbol)
    if not secids:
        raise LookupError(f"Eastmoney does not support symbol: {symbol}")
    last_error = None
    for secid in secids:
        try:
            payload = fetch_json(
                f"https://push2.eastmoney.com/api/qt/stock/get?secid={secid}&fields=f43,f57,f58"
            )
            data = payload.get("data") or {}
            raw_price = data.get("f43")
            if raw_price in (None, "", "-"):
                raise LookupError(f"No quote found for symbol: {symbol}")
            scale = get_eastmoney_price_scale(symbol)
            return make_quote(symbol, float(raw_price) / scale, infer_currency_from_symbol(symbol), "eastmoney")
        except Exception as exc:
            last_error = exc
    raise last_error if last_error is not None else LookupError(f"No quote found for symbol: {symbol}")


def fetch_tencent_quote(symbol: str) -> dict:
    provider_symbol = get_tencent_symbol(symbol)
    if provider_symbol is None:
        raise LookupError(f"Tencent does not support symbol: {symbol}")
    payload = fetch_text(f"https://qt.gtimg.cn/q={provider_symbol}", headers=TENCENT_HTTP_HEADERS, encoding="gbk")
    match = re.search(r'="([^"]+)";', payload)
    if not match:
        raise LookupError(f"No quote found for symbol: {symbol}")
    fields = match.group(1).split("~")
    if len(fields) < 4 or not fields[3]:
        raise LookupError(f"No quote found for symbol: {symbol}")
    return make_quote(symbol, float(fields[3]), infer_currency_from_symbol(symbol), "tencent")


def fetch_sina_quote(symbol: str) -> dict:
    provider_symbol = get_sina_symbol(symbol)
    if provider_symbol is None:
        raise LookupError(f"Sina does not support symbol: {symbol}")
    provider_symbols = [provider_symbol]
    hk_code = get_hk_provider_code(symbol)
    if hk_code is not None:
        provider_symbols.append(f"hk{hk_code}")

    last_error = None
    for current_provider_symbol in provider_symbols:
        try:
            payload = fetch_text(
                f"https://hq.sinajs.cn/list={current_provider_symbol}", headers=SINA_HTTP_HEADERS, encoding="gbk"
            )
            match = re.search(r'="([^"]*)";', payload)
            if not match:
                raise LookupError(f"No quote found for symbol: {symbol}")
            fields = match.group(1).split(",")
            if len(fields) < 4 or not fields[3]:
                raise LookupError(f"No quote found for symbol: {symbol}")
            return make_quote(symbol, float(fields[3]), infer_currency_from_symbol(symbol), "sina")
        except Exception as exc:
            last_error = exc
    raise last_error if last_error is not None else LookupError(f"No quote found for symbol: {symbol}")


def fetch_quote_from_domestic_sources(symbol: str) -> dict:
    last_error = None
    if is_hk_symbol(symbol):
        fetchers = (fetch_eastmoney_quote, fetch_sina_quote, fetch_tencent_quote)
    else:
        fetchers = (fetch_eastmoney_quote, fetch_tencent_quote, fetch_sina_quote)
    for fetcher in fetchers:
        try:
            return fetcher(symbol)
        except Exception as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise LookupError(f"No quote found for symbol: {symbol}")


def get_yfinance_batch_quotes(symbols: List[str]) -> Dict[str, dict]:
    with suppress_external_noise():
        history = get_yfinance().download(
            " ".join(symbols),
            period="1d",
            interval="1m",
            progress=False,
            threads=False,
            group_by="ticker",
            auto_adjust=False,
        )

    quotes = {}
    for symbol in symbols:
        price = extract_latest_close(history, symbol)
        quotes[symbol] = make_quote(symbol, price, infer_currency_from_symbol(symbol), "yfinance")
    return quotes


def get_yfinance_quote(symbol: str) -> dict:
    with suppress_external_noise():
        ticker = get_yfinance().Ticker(symbol)
        info = ticker.fast_info
        price = info.get("lastPrice") or info.get("regularMarketPrice")
        currency = info.get("currency")

        if price is None:
            history = ticker.history(period="1d", interval="1m")
            if history.empty:
                raise LookupError(f"No quote found for symbol: {symbol}")
            price = float(history["Close"].dropna().iloc[-1])

    return make_quote(symbol, price, currency, "yfinance")


def fetch_quotes(symbols: List[str]) -> Tuple[Dict[str, dict], Dict[str, Exception]]:
    cache = prune_quote_cache(load_quote_cache())
    cache_changed = False
    quotes: Dict[str, dict] = {}
    errors: Dict[str, Exception] = {}
    missing_symbols = []

    for symbol in symbols:
        cached_quote = get_cached_quote(cache, symbol)
        if cached_quote is not None:
            quotes[symbol] = cached_quote
        else:
            missing_symbols.append(symbol)

    yahoo_symbols = []
    for symbol in missing_symbols:
        if prefers_domestic_sources(symbol):
            try:
                quote = fetch_quote_from_domestic_sources(symbol)
            except Exception:
                yahoo_symbols.append(symbol)
            else:
                quotes[symbol] = quote
                set_cached_quote(cache, quote)
                cache_changed = True
        else:
            yahoo_symbols.append(symbol)

    if yahoo_symbols:
        try:
            yahoo_quotes = get_yfinance_batch_quotes(yahoo_symbols)
        except Exception:
            yahoo_quotes = {}

        for symbol in yahoo_symbols:
            quote = yahoo_quotes.get(symbol)
            if quote is None:
                try:
                    quote = get_yfinance_quote(symbol)
                except Exception as exc:
                    errors[symbol] = exc
                    continue
            quotes[symbol] = quote
            set_cached_quote(cache, quote)
            cache_changed = True

    if cache_changed:
        save_quote_cache(prune_quote_cache(cache))

    return quotes, errors


def fetch_batch_quotes(symbols: List[str], market: Optional[str]) -> dict:
    results = [None] * len(symbols)
    success_count = 0
    error_count = 0
    pending_results = []
    normalized_symbols = []

    for index, input_symbol in enumerate(symbols):
        try:
            resolved_symbol, resolved_market = resolve_symbol(input_symbol, market)
            normalized_symbol = normalize_symbol(resolved_symbol, resolved_market)
        except Exception as exc:
            results[index] = {"input": input_symbol, "error": format_error(exc)}
            error_count += 1
        else:
            pending_results.append((index, input_symbol, normalized_symbol))
            if normalized_symbol not in normalized_symbols:
                normalized_symbols.append(normalized_symbol)

    if normalized_symbols:
        batch_quotes, batch_errors = fetch_quotes(normalized_symbols)

        for index, input_symbol, normalized_symbol in pending_results:
            quote = batch_quotes.get(normalized_symbol)
            if quote is None:
                exc = batch_errors.get(normalized_symbol, LookupError(f"No quote found for symbol: {normalized_symbol}"))
                results[index] = \
                    {"input": input_symbol, "error": format_error(exc)}
                error_count += 1
            else:
                results[index] = {"input": input_symbol, **quote}
                success_count += 1

    return {
        "results": [result for result in results if result is not None],
        "success_count": success_count,
        "error_count": error_count,
    }


def get_quote(symbol: str) -> dict:
    quotes, errors = fetch_quotes([symbol])
    quote = quotes.get(symbol)
    if quote is not None:
        return quote
    raise errors.get(symbol, LookupError(f"No quote found for symbol: {symbol}"))


def emit(data: dict, exit_code: int = 0) -> None:
    json.dump(data, sys.stdout, ensure_ascii=True)
    sys.stdout.write("\n")
    raise SystemExit(exit_code)


def main() -> None:
    try:
        symbols, market = parse_request()
        if len(symbols) == 1:
            emit(fetch_quote_result(symbols[0], market))
        emit(fetch_batch_quotes(symbols, market))
    except ValueError as exc:
        emit({"error": {"code": "INVALID_INPUT", "message": str(exc)}}, 2)
    except AmbiguousSymbolError as exc:
        emit({"error": {"code": "AMBIGUOUS_SYMBOL", "message": str(exc)}}, 4)
    except LookupError as exc:
        emit({"error": {"code": "SYMBOL_NOT_FOUND", "message": str(exc)}}, 3)
    except Exception as exc:
        emit({"error": {"code": "UPSTREAM_ERROR", "message": str(exc)}}, 1)


if __name__ == "__main__":
    main()
