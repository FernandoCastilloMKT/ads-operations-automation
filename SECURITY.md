# Security Policy

## Public Showcase

This repository is a sanitized technical showcase. It is intentionally
disconnected from production and must remain free of GitHub Actions secrets,
real account configuration and operational logs.

Only CI is enabled. Files under `examples/workflows/` document the production
architecture but use the `.yml.example` extension and cannot run on GitHub.

## Prohibited Data

Do not include any of the following in issues, commits or pull requests:

- client or campaign names;
- advertising account, MCC or Google Sheets identifiers;
- internal email addresses;
- `.env`, OAuth tokens, service-account JSON or private keys;
- real `.clasp.json` files;
- execution output containing spend, balances or campaign performance.

Use the fictitious `config_*.example.json` files when demonstrating changes.

## Reporting A Security Issue

Do not open a public issue containing sensitive evidence. Contact the
repository owner privately and include only the minimum information required
to reproduce the problem.

If a secret is exposed, revoke it first. Removing it in a later commit is not
enough because it remains in Git history.
