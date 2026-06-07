# Contributing

Thanks for considering a contribution. This is a personal-data tool, so the
rules below are stricter than a typical OSS project — please read before
opening an issue or PR.

## Privacy ground rules

- **Do NOT paste real message content** into issues, PRs, commit messages,
  or any file in this repo. That includes your test exports, screenshots
  with names/phones, or `chat.db` extracts.
- **Reproduce bugs with synthetic data.** The test suite under `tests/`
  shows how to construct a fixture from scratch with stdlib `sqlite3`.
- If a bug only reproduces against your real DB, describe it in prose
  (column values, row counts, observed vs expected) — do not attach the DB
  or any export.
- The CI runs only the synthetic-fixture suite; no contributor's real
  `chat.db` is ever accessed.

## Setting up

```bash
git clone https://github.com/<your-fork>/imessage-history.git
cd imessage-history
# stdlib only — no install step required
python3 imessage_export.py --help
```

For console-script style (`imessage-export ...` instead of
`python3 imessage_export.py ...`):

```bash
pip install -e .
imessage-export --help
```

## Running the tests

```bash
python3 -m unittest discover -s tests -v
```

Tests are stdlib `unittest`. There are no extra dependencies and no pytest
fixtures — keep it that way if you add tests.

## Code style

- Python 3.10+, stdlib only. No new runtime dependencies.
- One file (`imessage_export.py`) — helpers grouped by section banner.
- Writers (`write_csv`, `write_json`, `write_txt`, `write_markdown`,
  `write_ai_ready`) take `(path, messages, metadata)` and don't share state.
- Use type hints on public functions.
- Prefer plain stdlib over clever metaprogramming.

## When changing the export schema

`sample_output_schema.md` and `README.md` document the JSON / CSV schema.
If you add or rename a field, update both — they're the contract downstream
tools depend on.

## When changing `chat.db` reads

- Stay read-only. The connection is opened with `mode=ro&immutable=1` and
  `PRAGMA query_only = ON`. The `OpenDbReadOnlyTests` suite must continue
  to pass.
- New columns? Run `PRAGMA table_info(message)` on a real `chat.db` first to
  confirm the column exists across macOS versions you care about.

## Reporting bugs

Open a GitHub issue using the **Bug report** template. Required info:

- macOS version
- Python version (`python3 --version`)
- What you ran (`imessage_export.py --...`) with phone numbers / chat IDs
  redacted to `<phone>` / `<chat-id>`
- What you expected
- What happened — describe in prose, do not paste exports

## Pull requests

- Make sure `python3 -m unittest discover -s tests -v` passes.
- Add tests for new behavior. Decoder / schema-handling changes should land
  with at least one regression test.
- Keep diffs focused. A "rendering bug fix" PR shouldn't also rewrite the
  CLI argparse setup.
- Update `README.md`, `CLAUDE.md`, or `sample_output_schema.md` if your
  change affects them.
- Use the **Pull request** template — it includes the privacy checkbox.

## Security

For anything that could leak data or break the read-only DB guards, see
[SECURITY.md](SECURITY.md). Don't open a public issue.
