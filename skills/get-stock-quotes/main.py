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
from typing import List, Optional, Tuple


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


def get_cache_path() -> str:
    skill_root = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(skill_root, ".cache", "a_share_symbols.json")


def fetch_json(url: str) -> dict:
    request = urllib.request.Request(url, headers=DEFAULT_HTTP_HEADERS)
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def load_a_share_cache() -> Optional[dict]:
    cache_path = get_cache_path()
    if not os.path.exists(cache_path):
        return None
    try:
        with open(cache_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return None

    if not isinstance(payload, dict) or not isinstance(payload.get("entries"), list):
        return None
    return payload


def save_a_share_cache(entries: list) -> None:
    cache_path = get_cache_path()
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as handle:
        json.dump({"fetched_at": time.time(), "entries": entries}, handle, ensure_ascii=False)


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


def fetch_batch_quotes(symbols: List[str], market: Optional[str]) -> dict:
    results = []
    success_count = 0
    error_count = 0

    for input_symbol in symbols:
        try:
            quote = fetch_quote_result(input_symbol, market)
        except Exception as exc:
            results.append({"input": input_symbol, "error": format_error(exc)})
            error_count += 1
        else:
            results.append({"input": input_symbol, **quote})
            success_count += 1

    return {
        "results": results,
        "success_count": success_count,
        "error_count": error_count,
    }


def get_quote(symbol: str) -> dict:
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

    return {
        "symbol": symbol,
        "price": float(price),
        "currency": currency or "UNKNOWN",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": "yfinance",
    }


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
