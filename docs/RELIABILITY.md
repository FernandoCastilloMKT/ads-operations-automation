# Reliability boundaries

Consumption refresh stops with an actionable error if a configured daily sheet
cannot be found. It does not replace an existing monthly amount with an empty
value. A hidden sheet remains readable; a renamed or deleted sheet needs recovery.

Monthly renewal writes the new end date, any carried budget and the idempotency
note in a single Sheets batch request. A rejected batch leaves all renewal fields
unchanged. If a response is lost after the server commits, the next run reads the
note and avoids extending the contract again. Other sheet updates are not claimed
to form one transaction across the whole execution.

The pure helper in `scripts/automation/smm_schedule.py` selects the UTC event for the operational date and
timezone, instead of comparing the runner start to an exact minute. A daily attempt
marker prevents automatic repeated calls; an explicit manual recovery remains
possible. Its decisions are tested locally without GitHub or advertising access.
The social workflows and private configuration are not shipped here.

The example is not an exactly-once distributed system. Separate computers and
remote runners require shared coordination if simultaneous writes must be prevented.
The embedded dry-run flag in consumption code does not protect every structural
operation; do not use it as a guarantee of no writes. Tests use simulated sheets.
