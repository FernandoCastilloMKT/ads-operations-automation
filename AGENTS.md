# Working on this public example

This is a sanitized architecture showcase. Start with README.md and
docs/ARCHITECTURE.md. Reliability behavior is described in docs/RELIABILITY.md.

- Use example configuration and fictional identifiers in tests and documentation.
- Never add credentials, real account mappings, customer information, logs or
  deployment state. Do not connect this repository to production.
- Only CI belongs in .github/workflows. Operational workflows are inert examples.
- Test with `python -m pytest -q`; run `python scripts/audit_public_repository.py --root .`.
- A missing daily sheet must stop monthly replacement; renewal dates and markers
  must be committed together. Preserve read-only advertising access.
- This tree is generated from an explicit allowlist. Changes to the source project
  must be exported and audited before updating this independent public history.
