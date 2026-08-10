import json
import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
CONSUMPTION_CONFIG_ENV = "ADS_AUTOMATION_CONSUMPTION_CONFIG_PATH"
SEM_CONFIG_ENV = "ADS_AUTOMATION_SEM_CONFIG_PATH"


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
