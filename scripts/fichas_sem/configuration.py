"""SEM: configuration."""

from runtime_config import load_consumption_runtime_config
from .constants import (
    ALL_SEM_CLIENTS_VALUE,
)
from .runtime import (
    EXCLUDED_SEM_CLIENTS,
    SEM_CLIENTS,
    SEM_ONLY_MCC_CONFIGS,
)

from .text import (
    normalize_customer_id,
    normalize_sem_client_selector,
    normalize_text,
)


def get_sem_customer_ids(sem_client):
    configured_ids = sem_client.get("google_ads_customer_ids")
    if configured_ids is None:
        configured_ids = [sem_client.get("google_ads_customer_id")]

    customer_ids = []
    seen_ids = set()
    for value in configured_ids:
        customer_id = normalize_customer_id(value)
        if customer_id and customer_id not in seen_ids:
            customer_ids.append(customer_id)
            seen_ids.add(customer_id)

    if not customer_ids:
        raise ValueError(
            f"La ficha {sem_client['nombre']} no tiene cuentas Google Ads."
        )
    return customer_ids


def get_sem_account_client_configs(sem_client):
    """Resolve explicit per-account manager overrides before any Sheet mutation."""
    customer_ids = get_sem_customer_ids(sem_client)
    overrides = sem_client.get("google_ads_mcc_by_customer_id", {})
    if not isinstance(overrides, dict):
        raise ValueError("google_ads_mcc_by_customer_id debe ser un objeto.")
    normalized = {}
    for raw_customer, raw_manager in overrides.items():
        customer_id = normalize_customer_id(raw_customer)
        manager_id = normalize_customer_id(raw_manager)
        if customer_id not in customer_ids or len(manager_id) != 10:
            raise ValueError("Gestor por cuenta no valido o cuenta fuera de la ficha.")
        if customer_id in normalized:
            raise ValueError("Cuenta duplicada en la asignacion de gestores.")
        normalized[customer_id] = manager_id
    if overrides and sem_client.get("handler") == "annual_campaign_control":
        raise ValueError("Los gestores por cuenta requieren una ficha SEM estandar.")
    manager_ids = {cid: normalized.get(cid, normalize_customer_id(sem_client["mcc_id"]))
                   for cid in customer_ids}
    configs = {mid: load_client_config_by_mcc(mid) for mid in set(manager_ids.values())}
    return {cid: configs[mid] for cid, mid in manager_ids.items()}


def select_sem_client(cliente_filter):
    normalized_filter = normalize_sem_client_selector(cliente_filter)

    if normalize_text(normalized_filter) in EXCLUDED_SEM_CLIENTS:
        raise SystemExit(
            "Cliente SEM excluido de momento por estructura especial: "
            f"{cliente_filter}"
        )

    for internal_key, client_config in SEM_CLIENTS.items():
        candidate_names = [
            internal_key,
            client_config["public_key"],
            client_config["nombre"],
            client_config["worksheet_name"],
            *client_config.get("aliases", []),
        ]

        for candidate_name in candidate_names:
            normalized_name = normalize_sem_client_selector(candidate_name)
            if normalized_filter == normalized_name:
                return client_config

    available = ", ".join(item["nombre"] for item in SEM_CLIENTS.values())
    raise SystemExit(
        "Cliente SEM no soportado en este piloto. "
        f"Disponibles: {available}"
    )


def resolve_sem_client_selection(cliente_filter):
    normalized_filter = normalize_sem_client_selector(cliente_filter)
    process_all = normalized_filter == normalize_sem_client_selector(
        ALL_SEM_CLIENTS_VALUE
    )
    if process_all:
        return True, list(SEM_CLIENTS.values())
    return False, [select_sem_client(cliente_filter)]


def load_client_config_by_mcc(mcc_id):
    config = load_consumption_runtime_config()

    wanted_mcc_id = normalize_customer_id(mcc_id)

    for cliente in config.get("clientes", []):
        if normalize_customer_id(cliente.get("mcc_id")) == wanted_mcc_id:
            return cliente

    sem_only_config = SEM_ONLY_MCC_CONFIGS.get(wanted_mcc_id)
    if sem_only_config:
        return sem_only_config

    raise SystemExit(
        f"No se encontro MCC {mcc_id} en config_clientes.json ni en la "
        "configuracion exclusiva de fichas SEM."
    )
