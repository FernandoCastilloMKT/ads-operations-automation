import json
import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
CONSUMPTION_CONFIG_ENV = "ADS_AUTOMATION_CONSUMPTION_CONFIG_PATH"
SEM_CONFIG_ENV = "ADS_AUTOMATION_SEM_CONFIG_PATH"
META_DYNAMIZATIONS_CONFIG_ENV = (
    "ADS_AUTOMATION_META_DYNAMIZATIONS_CONFIG_PATH"
)


class RuntimeConfigError(RuntimeError):
    pass


def normalize_config_key(value):
    return "".join(
        character.lower()
        for character in str(value or "")
        if character.isalnum()
    )


def _resolve_config_path(environment_name, default_filename):
    configured_path = os.getenv(environment_name, "").strip()
    if not configured_path:
        return BASE_DIR / default_filename

    path = Path(configured_path).expanduser()
    if not path.is_absolute():
        path = BASE_DIR / path
    return path.resolve()


def _load_json_config(environment_name, default_filename, label):
    path = _resolve_config_path(environment_name, default_filename)
    if not path.is_file():
        raise RuntimeConfigError(
            f"No existe la configuracion de {label}. "
            f"Defina {environment_name} o cree {default_filename}."
        )

    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeConfigError(
            f"No se pudo leer la configuracion de {label}."
        ) from exc

    if not isinstance(config, dict):
        raise RuntimeConfigError(
            f"La configuracion de {label} debe ser un objeto JSON."
        )
    return config


def load_consumption_runtime_config():
    config = _load_json_config(
        CONSUMPTION_CONFIG_ENV,
        "config_clientes.json",
        "consumos",
    )
    if not isinstance(config.get("clientes"), list):
        raise RuntimeConfigError(
            "La configuracion de consumos debe incluir una lista 'clientes'."
        )
    return config


def load_sem_runtime_config():
    config = _load_json_config(
        SEM_CONFIG_ENV,
        "config_fichas_sem.json",
        "fichas SEM",
    )
    clients = config.get("clients")
    if not config.get("control_sem_spreadsheet_id"):
        raise RuntimeConfigError(
            "La configuracion SEM no incluye control_sem_spreadsheet_id."
        )
    if not isinstance(clients, dict) or not clients:
        raise RuntimeConfigError(
            "La configuracion SEM debe incluir un objeto 'clients' no vacio."
        )
    public_keys = set()
    for internal_key, client in clients.items():
        if not isinstance(client, dict):
            raise RuntimeConfigError(
                f"La ficha {internal_key!r} debe ser un objeto JSON."
            )
        required = ("nombre", "worksheet_name", "mcc_id", "public_key")
        missing = [field for field in required if not client.get(field)]
        if missing:
            raise RuntimeConfigError(
                f"La ficha {internal_key!r} no incluye campos obligatorios."
            )
        public_key = str(client["public_key"]).strip().lower()
        if not public_key.startswith("client_") or public_key in public_keys:
            raise RuntimeConfigError(
                "Cada ficha SEM debe tener una public_key unica con formato "
                "client_NNN."
            )
        public_keys.add(public_key)

    return config


def _resolve_meta_account_references(clients):
    resolved_accounts = {}

    def resolve_account(internal_key, trail=()):
        if internal_key in resolved_accounts:
            return resolved_accounts[internal_key]
        if internal_key in trail:
            raise RuntimeConfigError(
                "La configuracion Meta contiene un ciclo en "
                "meta_ad_account_from."
            )
        client = clients.get(internal_key)
        if client is None:
            raise RuntimeConfigError(
                f"La referencia de cuenta Meta {internal_key!r} no existe."
            )
        account_id = client.get("meta_ad_account_id")
        if not account_id:
            account_id = resolve_account(
                str(client["meta_ad_account_from"]),
                trail + (internal_key,),
            )
        resolved_accounts[internal_key] = str(account_id).strip()
        return resolved_accounts[internal_key]

    for internal_key, client in clients.items():
        client["meta_ad_account_id"] = resolve_account(internal_key)


def _validate_meta_dynamizations_layout(
    internal_key,
    layout,
    row_breakdown=None,
):
    required = (
        "snapshot_start_row",
        "snapshot_end_row",
        "current_title_row",
        "current_header_row",
        "current_data_row",
        "history_year_row",
        "history_header_row",
        "history_first_data_row",
        "history_last_data_row",
        "history_spacer_row",
    )
    missing = [field for field in required if field not in layout]
    if missing:
        raise RuntimeConfigError(
            f"La dinamizacion {internal_key!r} no define filas: "
            + ", ".join(missing)
        )

    try:
        rows = {field: int(layout[field]) for field in required}
        current_last = int(
            layout.get("current_data_last_row", rows["current_data_row"])
        )
    except (TypeError, ValueError) as exc:
        raise RuntimeConfigError(
            f"La dinamizacion {internal_key!r} contiene una fila no valida."
        ) from exc

    row_stride = max(1, len((row_breakdown or {}).get("rows", ())))
    valid = (
        rows["snapshot_start_row"] > 0
        and rows["current_title_row"] == rows["snapshot_start_row"]
        and rows["current_header_row"] == rows["current_title_row"] + 1
        and rows["current_data_row"] == rows["current_header_row"] + 1
        and rows["current_data_row"] <= current_last
        and current_last < rows["history_year_row"]
        and rows["history_header_row"] == rows["history_year_row"] + 1
        and rows["history_first_data_row"] == rows["history_header_row"] + 1
        and rows["history_last_data_row"]
        == rows["history_first_data_row"] + 12 * row_stride - 1
        and rows["history_spacer_row"]
        == rows["history_last_data_row"] + 1
        and rows["snapshot_end_row"] >= rows["history_spacer_row"]
    )
    if not valid:
        raise RuntimeConfigError(
            f"La dinamizacion {internal_key!r} no respeta el contrato de "
            "bloques y fila separadora."
        )


def load_meta_dynamizations_runtime_config():
    config = _load_json_config(
        META_DYNAMIZATIONS_CONFIG_ENV,
        "config_meta_ads_dinamizaciones.json",
        "dinamizaciones Meta Ads",
    )
    clients = config.get("clients")
    if not config.get("spreadsheet_id"):
        raise RuntimeConfigError(
            "La configuracion Meta no incluye spreadsheet_id."
        )
    if not isinstance(clients, dict) or not clients:
        raise RuntimeConfigError(
            "La configuracion Meta debe incluir un objeto 'clients' no vacio."
        )

    for internal_key, client in clients.items():
        if not isinstance(client, dict):
            raise RuntimeConfigError(
                f"La dinamizacion {internal_key!r} debe ser un objeto JSON."
            )
        required = (
            "nombre",
            "worksheet_name",
            "campaign_name_contains",
            "monthly_budget_eur",
            "history_start_month",
            "layout",
        )
        missing = [field for field in required if not client.get(field)]
        if missing:
            raise RuntimeConfigError(
                f"La dinamizacion {internal_key!r} no incluye campos "
                "obligatorios: "
                + ", ".join(missing)
            )
        if not isinstance(client["campaign_name_contains"], list):
            raise RuntimeConfigError(
                f"La dinamizacion {internal_key!r} debe configurar una lista "
                "campaign_name_contains."
            )
        if not all(
            str(fragment).strip()
            for fragment in client["campaign_name_contains"]
        ):
            raise RuntimeConfigError(
                f"La dinamizacion {internal_key!r} contiene un filtro de "
                "campana vacio."
            )
        action_type_fields = (
            "reaction_action_types",
            "result_action_types",
            "interaction_action_types",
            "like_action_types",
            "comment_action_types",
            "share_action_types",
            "save_action_types",
            "follow_action_types",
        )
        for field in action_type_fields:
            values = client.get(field)
            if values is not None and (
                not isinstance(values, list)
                or not values
                or not all(str(value).strip() for value in values)
            ):
                raise RuntimeConfigError(
                    f"La dinamizacion {internal_key!r} contiene {field} "
                    "no valido."
                )
        campaign_discovery = client.get("campaign_discovery", "catalog")
        if campaign_discovery not in {"catalog", "insights_with_activity"}:
            raise RuntimeConfigError(
                f"La dinamizacion {internal_key!r} contiene un metodo de "
                "descubrimiento de campanas no valido."
            )
        budget_overrides = client.get("monthly_budget_eur_by_period", {})
        if not isinstance(budget_overrides, dict):
            raise RuntimeConfigError(
                f"La dinamizacion {internal_key!r} contiene excepciones de "
                "presupuesto no validas."
            )
        for period_key, budget in budget_overrides.items():
            valid_period = (
                isinstance(period_key, str)
                and len(period_key) == 7
                and period_key[4] == "-"
                and period_key[:4].isdigit()
                and period_key[5:].isdigit()
                and 1 <= int(period_key[5:]) <= 12
            )
            try:
                valid_budget = float(budget) >= 0
            except (TypeError, ValueError):
                valid_budget = False
            if not valid_period or not valid_budget:
                raise RuntimeConfigError(
                    f"La dinamizacion {internal_key!r} contiene una "
                    "excepcion mensual de presupuesto no valida."
                )
        has_account_id = bool(client.get("meta_ad_account_id"))
        has_account_reference = bool(client.get("meta_ad_account_from"))
        if has_account_id == has_account_reference:
            raise RuntimeConfigError(
                f"La dinamizacion {internal_key!r} debe configurar exactamente "
                "uno de meta_ad_account_id o meta_ad_account_from."
            )
        breakdown = client.get("row_breakdown")
        aggregate_breakdown = client.get(
            "aggregate_breakdown_in_meta",
            False,
        )
        if not isinstance(aggregate_breakdown, bool) or (
            aggregate_breakdown and not breakdown
        ):
            raise RuntimeConfigError(
                f"La dinamizacion {internal_key!r} contiene una agregacion "
                "de desglose no valida."
            )
        omit_zero_breakdown_rows = client.get(
            "omit_zero_breakdown_rows",
            False,
        )
        if not isinstance(omit_zero_breakdown_rows, bool) or (
            omit_zero_breakdown_rows and not breakdown
        ):
            raise RuntimeConfigError(
                f"La dinamizacion {internal_key!r} contiene una omision de "
                "filas sin actividad no valida."
            )
        if breakdown:
            rows = breakdown.get("rows")
            if not breakdown.get("header") or not isinstance(rows, list) or len(rows) < 2:
                raise RuntimeConfigError(
                    f"La dinamizacion {internal_key!r} contiene un desglose "
                    "de filas no valido."
                )
            keys = [str(row.get("key") or "").strip() for row in rows]
            labels = [str(row.get("label") or "").strip() for row in rows]
            if not all(keys) or len(set(keys)) != len(keys) or not all(labels):
                raise RuntimeConfigError(
                    f"La dinamizacion {internal_key!r} contiene filas de "
                    "desglose ambiguas."
                )
        campaign_ids = client.get("meta_campaign_ids")
        if campaign_ids is not None and (
            not isinstance(campaign_ids, list)
            or not campaign_ids
            or not all(str(value).strip().isdigit() for value in campaign_ids)
        ):
            raise RuntimeConfigError(
                f"La dinamizacion {internal_key!r} contiene IDs de campana "
                "no validos."
            )
        expected_campaign_count = client.get("expected_campaign_count")
        if expected_campaign_count is not None and (
            not isinstance(expected_campaign_count, int)
            or isinstance(expected_campaign_count, bool)
            or expected_campaign_count < 1
        ):
            raise RuntimeConfigError(
                f"La dinamizacion {internal_key!r} contiene un numero "
                "esperado de campanas no valido."
            )
        max_campaign_count = client.get("max_campaign_count")
        if max_campaign_count is not None and (
            not isinstance(max_campaign_count, int)
            or isinstance(max_campaign_count, bool)
            or max_campaign_count < 1
        ):
            raise RuntimeConfigError(
                f"La dinamizacion {internal_key!r} contiene un maximo de "
                "campanas no valido."
            )
        metric_start_column = str(
            client.get("metric_start_column", "B")
        ).strip()
        metric_column_search_radius = client.get(
            "metric_column_search_radius",
            0,
        )
        if (
            not metric_start_column
            or not metric_start_column.isalpha()
            or not isinstance(metric_column_search_radius, int)
            or isinstance(metric_column_search_radius, bool)
            or not 0 <= metric_column_search_radius <= 3
        ):
            raise RuntimeConfigError(
                f"La dinamizacion {internal_key!r} contiene una busqueda de "
                "columnas no valida."
            )
        _validate_meta_dynamizations_layout(
            internal_key,
            client["layout"],
            breakdown,
        )

    _resolve_meta_account_references(clients)
    return config
