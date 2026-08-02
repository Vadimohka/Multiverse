# Contributing to Multiverse

Thanks for helping improve Multiverse. Before opening a large feature request, create an issue with a minimal reproducible input, expected output, proposed workflow/API behaviour, and any security or compatibility implications.

## Local checks

Use CPython 3.14.6 (normal GIL-enabled build) and Node.js 24+. From the repository root:

```bash
python3.14 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
make test
make lint
make frontend-build
```

For a container-based local run, use `make up`. Never commit `.env`, artifacts, databases, captured pages, or credentials.

## Pull requests

- Keep each pull request focused and include tests for behaviour changes.
- Update user-facing documentation when you add or change a node, configuration, or integration.
- Follow the existing formatting and type-checking rules.
- Confirm that CI is green before requesting review.

By participating, you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).
