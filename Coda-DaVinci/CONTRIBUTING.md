# Contributing

Use Python 3.11 or 3.12 and install `pip install -e '.[dev]'`.
Run `pytest -q` and `python -m build` before submitting a change.

Keep changes focused on the core workflow. Add a regression test when changing
routing, media binding, tool permissions or delivery rules. Provider adapters
should preserve submitted inputs and request IDs, document their protocol and
report failures at the relevant stage.

Include the problem, resulting behavior and relevant verification in pull requests.
Use synthetic examples in tests and remove credentials from logs before sharing.
Contributions are licensed under Apache-2.0.
