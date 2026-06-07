<!--
⚠️ Privacy reminder: Do NOT include real message content, real phone
numbers, real screenshots, or real export files in this PR. Use the
synthetic-fixture patterns in tests/ if you need example data.
-->

## What this changes

<!-- One paragraph. What and why. -->

## How I tested

```bash
python3 -m unittest discover -s tests -v
# Plus anything else you ran.
```

## Checklist

- [ ] `python3 -m unittest discover -s tests -v` passes locally
- [ ] No new runtime dependencies (stdlib only)
- [ ] No real message content / PII anywhere in the diff
- [ ] If the schema (JSON / CSV) changed, `sample_output_schema.md` and
      `README.md` are updated
- [ ] If `chat.db` access changed, `OpenDbReadOnlyTests` still passes and
      the read-only guards in `open_db()` are intact
- [ ] If a new CLI flag landed, `README.md` and the module docstring at
      the top of `imessage_export.py` are updated

## Related issue

<!-- Closes #N, refs #N, or "none". -->
