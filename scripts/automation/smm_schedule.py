"""Selecciona el cron SMM por fecha y zona, sin exigir puntualidad al runner."""

import argparse
import os
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo


def scheduled_context(event_name, event_schedule, now):
    local = now.astimezone(ZoneInfo("Europe/Madrid"))
    scheduled = datetime.combine(local.date(), time(9, 20), local.tzinfo)
    utc = scheduled.astimezone(timezone.utc)
    expected_cron = f"20 {utc.hour} * * *"
    candidate = event_name == "workflow_dispatch" or (
        event_name == "schedule" and event_schedule == expected_cron
    )
    return {
        "candidate": str(candidate).lower(),
        "marker_key": f"smm-attempt-{local.date().isoformat()}",
        "operational_date": local.date().isoformat(),
    }


def should_run(candidate, event_name, origin, attempted):
    if not candidate:
        return False
    explicit_manual = event_name == "workflow_dispatch" and origin != "windows-fallback"
    return explicit_manual or not attempted


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--decide", action="store_true")
    args = parser.parse_args()
    if args.decide:
        allowed = should_run(
            os.environ["CANDIDATE"] == "true",
            os.environ["EVENT_NAME"],
            os.environ.get("REQUEST_ORIGIN", ""),
            os.environ.get("ATTEMPTED", "") == "true",
        )
        result = {"should_run": str(allowed).lower()}
        print("Ejecutar SMM." if allowed else "SMM omitido: cobertura UTC o intento diario existente.")
    else:
        result = scheduled_context(
            os.environ["EVENT_NAME"],
            os.environ.get("EVENT_SCHEDULE", ""),
            datetime.now(timezone.utc),
        )
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        for key, value in result.items():
            output.write(f"{key}={value}\n")
    if not args.decide:
        print(f"Fecha operativa SMM: {result['operational_date']}; "
              f"candidato: {result['candidate']}.")


if __name__ == "__main__":
    main()
