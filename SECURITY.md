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

## Publication Controls

The public tree is rebuilt from an explicit allowlist. Before publication, the
audit rejects known private configuration values and high-confidence credential
formats, including Google API keys and OAuth client IDs, Meta access tokens,
GitHub tokens, JWTs and private keys. CI then runs against fictitious
configuration without contacting external services.

This scanning is a final guardrail, not a reason to copy private material into
the build directory. Review the generated diff and repository visibility before
every push.

Run `python scripts/audit_public_repository.py --root . --history=--all` from
a complete independent clone. History checks include deleted text, commit
metadata and prohibited historical filenames. The scan reports rule names and
locations, not matched credentials. Confidential names must be supplied locally
with `--deny-term` or `--private-reference`; do not commit the audit denylist.
Checks also cover common OAuth secrets, refresh tokens, API keys, deployment
artifacts and advertising identifiers. Review binary media, repository metadata,
Issues, pull requests and Actions logs separately: regex scans are not a guarantee
that every secret or personal detail has been detected.

## Reporting A Security Issue

Do not open a public issue containing sensitive evidence. Contact the
repository owner privately and include only the minimum information required
to reproduce the problem.

If a secret is exposed, revoke it first. Removing it in a later commit is not
enough because it remains in Git history.
