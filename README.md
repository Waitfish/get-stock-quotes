# get-stock-quotes

Hermes skill repository for fetching stock quotes.

## Install

Install the published skill with Hermes using the published `SKILL.md` URL:

```bash
hermes skills install "https://waitfish.github.io/get-stock-quotes/.well-known/skills/get-stock-quotes/SKILL.md" --yes
```

Alternative GitHub / skills.sh install path:

```bash
hermes skills install "skills-sh/Waitfish/get-stock-quotes/skills/get-stock-quotes" --yes
```

Check that Hermes sees it:

```bash
hermes skills check
```

Installed skill location:

```text
~/.hermes/skills/get-stock-quotes
```

## Hermes Chat Example

Call the skill explicitly in Hermes chat:

```bash
hermes chat -Q -s "get-stock-quotes" -q "请使用 get-stock-quotes 技能查询 万科A 和 闻泰科技 的最新行情，并返回结构化结果。"
```

You can also try natural language without preloading the skill:

```bash
hermes chat -Q -q "查询 万科A 和 闻泰科技 的最新行情。"
```

## Published Endpoints

- Well-known index: `https://waitfish.github.io/get-stock-quotes/.well-known/skills/index.json`
- Well-known skill: `https://waitfish.github.io/get-stock-quotes/.well-known/skills/get-stock-quotes/SKILL.md`
- GitHub repository: `https://github.com/Waitfish/get-stock-quotes`

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
