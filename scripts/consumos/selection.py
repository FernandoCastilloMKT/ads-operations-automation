"""Consumption: selection."""

from .text import (
    normalize_match_text,
)


def normalize_run_mode(value):
    normalized = normalize_match_text(value)

    if normalized in {"completo", "complete", "full"}:
        return "completo"

    if normalized in {"rapido", "quick", "fast"}:
        return "rapido"

    raise SystemExit("Modo no valido. Usa --modo completo o --modo rapido.")


def client_matches_filters(cliente, filters):
    if not filters:
        return cliente.get("activo") is True

    client_values = [
        cliente.get("nombre", ""),
        cliente.get("mcc_id", ""),
    ]
    client_values.extend(cliente.get("sub_mcc_ids", []))

    for source in cliente.get("manager_sources", []):
        client_values.extend([
            source.get("nombre", ""),
            source.get("mcc_id", ""),
        ])
        client_values.extend(source.get("sub_mcc_ids", []))

    normalized_values = [
        normalize_match_text(value)
        for value in client_values
    ]

    for raw_filter in filters:
        normalized_filter = normalize_match_text(raw_filter)

        if not normalized_filter:
            continue

        if any(
            normalized_filter in value or value in normalized_filter
            for value in normalized_values
            if value
        ):
            return True

    return False


def select_clients(config, filters):
    """
    Sin filtro, procesa solo clientes activos.
    Con --cliente, permite aislar un cliente concreto aunque este marcado
    como inactivo; asi podemos probar MCC por MCC sin tocar los demas.
    """
    clientes = config.get("clientes", [])
    if filters:
        matched = set()
        for raw_filter in filters:
            exact = [index for index, cliente in enumerate(clientes)
                     if normalize_match_text(cliente.get("nombre", ""))
                     == normalize_match_text(raw_filter)]
            matched.update(exact or [
                index for index, cliente in enumerate(clientes)
                if client_matches_filters(cliente, [raw_filter])
            ])
        selected = [cliente for index, cliente in enumerate(clientes) if index in matched]
    else:
        selected = [cliente for cliente in clientes if cliente.get("activo") is True]

    if not selected:
        available = ", ".join(
            cliente.get("nombre", "(sin nombre)")
            for cliente in clientes
        )
        raise SystemExit(
            "No hay clientes que coincidan con el filtro indicado. "
            f"Clientes disponibles: {available}"
        )

    return selected
