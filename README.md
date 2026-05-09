# get-stock-quotes

Hermes skill repository for fetching stock quotes.

## Layout

- `skills/get-stock-quotes/`: Hermes-discoverable skill directory
- `tests/`: local regression tests for the Python entrypoint

## Local development

Install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r skills/get-stock-quotes/requirements.txt
```

Run the skill directly:

```bash
python skills/get-stock-quotes/main.py --symbol 万科A
python skills/get-stock-quotes/main.py --symbols "万科A,闻泰科技,北京君正"
```

Run tests:

```bash
python3 -m unittest discover -s tests -v
```
