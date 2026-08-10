import argparse
import base64
import gzip
import json
import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
MAX_DECOMPRESSED_BYTES = 2 * 1024 * 1024
TARGETS = {
    "consumption": {
        "destination": BASE_DIR / "config_clientes.json",
        "secret_env": "CONSUMPTION_CONFIG_GZIP_B64",
        "required_key": "clientes",
    },
    "sem": {
        "destination": BASE_DIR / "config_fichas_sem.json",
        "secret_env": "SEM_CONFIG_GZIP_B64",
        "required_key": "clients",
    },
}


def parse_args():
    parser = argparse.ArgumentParser(
        description="Materializa configuraciones privadas sin mostrarlas."
    )
    parser.add_argument(
        "targets",
        nargs="+",
        choices=sorted(TARGETS),
    )
    return parser.parse_args()


def validate_json_bytes(raw, required_key):
    if len(raw) > MAX_DECOMPRESSED_BYTES:
        raise ValueError("La configuracion privada supera el limite permitido.")
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict) or required_key not in data:
        raise ValueError("La configuracion privada no tiene el esquema esperado.")


def materialize(target_name):
    target = TARGETS[target_name]
    destination = target["destination"]
    if destination.is_file():
        validate_json_bytes(
            destination.read_bytes(),
            target["required_key"],
        )
        print(f"Configuracion {target_name}: disponible.")
        return

    encoded = os.getenv(target["secret_env"], "").strip()
    if not encoded:
        raise SystemExit(
            f"Falta el secret {target['secret_env']} para {target_name}."
        )

    try:
        compressed = base64.b64decode(encoded, validate=True)
        raw = gzip.decompress(compressed)
        validate_json_bytes(raw, target["required_key"])
    except (ValueError, OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SystemExit(
            f"El secret de configuracion {target_name} no es valido."
        ) from exc

    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_bytes(raw)
    temporary.replace(destination)
    print(f"Configuracion {target_name}: materializada de forma segura.")


def main():
    args = parse_args()
    for target_name in args.targets:
        materialize(target_name)


if __name__ == "__main__":
    main()
