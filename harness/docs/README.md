# Documentation Harness

This folder holds lightweight tools for planning and auditing MkDocs work.

## Log Writer

Use `log_writer.py` when a documentation task has a meaningful PM checkpoint:

```bash
python harness/docs/log_writer.py "Added a learning path and PM workplan" \
  --phase docs-alignment \
  --files docs/learn/index.md,docs/documentation-workplan.md,mkdocs.yml \
  --metrics "Learning entry is visible in nav,Workplan has measurable acceptance criteria"
```

The writer appends JSONL records under `harness/docs/logs/` with a UTC timestamp.
Keep entries short and outcome-oriented so reviews stay cheap.

## Docs Check

Use `check_docs.py` before committing documentation changes:

```bash
python harness/docs/check_docs.py
```

It runs `mkdocs build --strict` and scans documentation pages for command patterns and overconfident safety language that have caused onboarding mistakes, such as old `--stack` syntax, assuming every Stackfile has a `default` task, or claiming safety-critical deployment readiness.
