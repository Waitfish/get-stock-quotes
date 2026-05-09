---
name: get-stock-quotes
description: Fetch stock quotes by ticker symbol with local Python code, including auto-normalization for A-share and Hong Kong symbols.
version: 0.1.0
author: Waitfish
license: MIT
metadata:
  entrypoint: python main.py
  runtime: python3.11
  inputs:
    - symbol
    - symbols
    - market
  hermes:
    tags:
      - finance
      - stocks
      - a-share
      - market-data
---

# Get Stock Quotes

Use this skill when the user wants the latest stock quote for a ticker symbol.

## Inputs

- `symbol`: ticker such as `AAPL`, `MSFT`, `600519`, `000001`, `700`, `0700`, `600519.SH`, `hk0700`, or a Chinese A-share name such as `万科A`, `闻泰科技`
- `symbols`: optional batch input as a list of symbols or names
- `market`: optional hint, one of `US`, `CN`, `HK`

## Behavior

- Run `python main.py --symbol <symbol>` to fetch the quote.
- Run `python main.py --symbols "<symbol1>,<symbol2>"` to fetch multiple quotes in one request.
- If the market is known and the symbol is ambiguous, add `--market <US|CN|HK>`.
- The script auto-normalizes common China and Hong Kong formats for Yahoo Finance.
- Chinese A-share names are resolved to 6-digit mainland symbols before quote lookup.
- Batch output returns a JSON object with `results`, `success_count`, and `error_count`.
- Return the JSON result directly unless the user asked for a formatted summary.

## Notes

- A-share examples: `600519 -> 600519.SS`, `000001 -> 000001.SZ`
- Hong Kong examples: `700 -> 0700.HK`, `00700 -> 0700.HK`
- If dependencies are not installed yet, run `pip install -r requirements.txt` first.
