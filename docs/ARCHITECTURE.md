# Automation Architecture

**English** | [Español](ARCHITECTURE.es.md)

## Scope

The solution replaces recurring Supermetrics queries when the operation needs
more control than a standard tabular extraction can provide. It does not aim
to reproduce every Supermetrics feature. It implements the specific SEM
workflows required for spend, performance, historical records, balances and
operational verification.

The public version preserves the technical decisions while removing all
business configuration.

## Layers

### 1. Data sources

- Google Ads API for accounts, campaigns and cost.
- Microsoft Advertising API for additional campaign and cost data.
- Google Calendar API for scheduled SEM actions.
- Google Sheets API for reading workbook contracts and writing results.

### 2. Python domain layer

Three scripts keep the main responsibilities separate:

- `actualizar_consumos.py`: daily and monthly account spend;
- `actualizar_fichas_sem.py`: performance tables and balance blocks;
- `verificar_acciones_calendario_sem.py`: post-action pause and reactivation
  checks.

Configuration is loaded from external JSON. The code receives opaque keys and
does not need to know real client names.

### 3. Orchestration

The production architecture combines two complementary mechanisms:

- GitHub Actions provides a reproducible environment that does not depend on
  an office computer remaining online.
- Google Apps Script provides schedules, in-sheet buttons, notifications and
  duplicate-request protection.

Apps Script requests an execution through `workflow_dispatch`; GitHub prepares
the environment and runs the corresponding Python service.

In this repository, operational workflows live under `examples/workflows/`
with the `.yml.example` extension. They document the automation but GitHub
does not recognize them as active workflows.

## Spend Control Flow

1. Load manager-account and worksheet configuration.
2. Discover final accounts without restricting results to `ENABLED`.
3. Query spend by account and date.
4. Aggregate several account hierarchies when required.
5. Detect headers, account IDs and date columns dynamically.
6. Refresh the current and previous month.
7. Write one matrix per block to reduce quota usage and HTTP 429 errors.
8. Create a new worksheet from a template when the month or year changes.

The account ID is the stable identity, not the account name. Running the
process twice updates existing rows instead of creating duplicates.

## SEM Workbook Flow

1. Select a workbook through an opaque client key.
2. Query its advertising accounts in parallel with controlled limits.
3. Normalize metrics from Google Ads and Microsoft Advertising.
4. Detect the live spreadsheet block through semantic labels and headers.
5. Adjust the number of rows while preserving formatting and one blank
   separator row.
6. Write campaign rows and totals in batches.
7. Update account state, actual spend and the active reporting period.
8. Archive the previous block in chronological order when the period changes.
9. Format periods as `Month YYYY`, remove dangling decimal separators and
   order rows by active status and descending cost.
10. Keep row placement under operator control while applying currency formats
   to live and historical aggregate costs.

Open-ended monthly renewals normally advance on the first day of the month.
If that run was missed, a later run safely catches up an end date from an
earlier month, while preserving manually entered budgets and never advancing
the current month before the next first day.

Exceptional behavior lives in configuration. This prevents client names from
becoming scattered conditional branches throughout the source code.

Apps Script also performs read-only spreadsheet checks. A fixed-term campaign
can produce one summary alert fourteen days before its end date. Weekend
notice dates move to the previous working day, open-ended monthly renewals are
excluded, and a property-backed key prevents duplicate notifications.

## Reliability

- Limited retries for transient API failures.
- Idempotent writes through stable IDs and deterministic ranges.
- Locks that prevent concurrent executions.
- Retrospective refreshes that capture billing adjustments.
- Detailed operational logs only inside private infrastructure.
- Simulated tests that never contact external APIs.
- Third-party GitHub Actions pinned to exact commit hashes.

## Public Model

This repository is a source-visible technical showcase:

- only CI is active;
- no GitHub Secrets are configured;
- production Apps Script is not connected to it;
- all configuration values are fictitious;
- each release is generated from an explicit allowlist;
- an auditor scans for credentials, IDs, emails, local paths and known private
  values before publication.

The real infrastructure, configuration and execution logs remain in a
separate private repository.
