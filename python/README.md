# vss2git (Python)

Runs on Linux and macOS. From this directory:

```bash
pip install -e '.[dev]'
```

Verbs are `migrate`, `list`, and `verify`. `--encoding` is a Windows code page and defaults to `1252`.

Tests read a database from `VSS2GIT_TEST_DB` and copy it before opening it. The tool does not write to a VSS database.
