# Hermes stock quote skill

This skill fetches a stock quote from Yahoo Finance by ticker symbol.

It also supports direct Chinese A-share names such as `万科A` and `闻泰科技`.

Hermes-compatible metadata lives in `SKILL.md`. The `skill.yaml` file is kept as local runtime metadata for this repository.

## Files

- `skill.yaml`: minimal Hermes skill metadata
- `SKILL.md`: Hermes skill definition with frontmatter
- `requirements.txt`: Python dependency list
- `main.py`: entrypoint that reads a symbol and prints JSON

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Usage

Command line:

```bash
python main.py --symbol AAPL
```

Command line with market hint:

```bash
python main.py --symbol 700 --market HK
```

Command line with Chinese A-share name:

```bash
python main.py --symbol 万科A
```

Batch command line:

```bash
python main.py --symbols "万科A,闻泰科技,北京君正"
```

JSON via stdin:

```bash
printf '{"symbol":"MSFT"}' | python main.py
```

Plain symbol via stdin:

```bash
printf 'TSLA' | python main.py
```

## Output

Success:

```json
{"symbol": "AAPL", "price": 189.84, "currency": "USD", "timestamp": "2026-05-06T13:45:00+00:00", "source": "yfinance"}
```

Failure:

```json
{"error": {"code": "SYMBOL_NOT_FOUND", "message": "No quote found for symbol: XXXX"}}
```

Batch success or partial success:

```json
{"results":[{"input":"万科A","symbol":"000002.SZ","price":4.0,"currency":"CNY","timestamp":"2026-05-09T00:00:00+00:00","source":"yfinance"},{"input":"不存在","error":{"code":"SYMBOL_NOT_FOUND","message":"No A-share symbol found for name: 不存在"}}],"success_count":1,"error_count":1}
```

## Symbol normalization

- `600519` -> `600519.SS`
- `000001` -> `000001.SZ`
- `700` -> `0700.HK`
- `00700` -> `0700.HK`
- `sh600519` -> `600519.SS`
- `hk0700` -> `0700.HK`
- `万科A` -> `000002.SZ`
- `闻泰科技` -> `600745.SS`

## Batch input

- `--symbols "万科A,闻泰科技,北京君正"`
- `printf '万科A\n闻泰科技' | python main.py`
- `printf '{"symbols":["万科A","闻泰科技"]}' | python main.py`
