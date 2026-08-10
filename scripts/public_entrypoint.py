import argparse
import os
import subprocess
import sys
import time
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(
        description="Ejecuta un componente sin publicar su salida privada."
    )
    parser.add_argument("--label", required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.command and args.command[0] == "--":
        args.command = args.command[1:]
    if not args.command:
        parser.error("Falta el comando que se debe ejecutar.")
    return args


def main():
    args = parse_args()
    started_at = time.monotonic()
    print(f"[runner] Iniciando componente: {args.label}.")
    public_mode = (
        (Path(__file__).resolve().parent.parent / ".public-runner").is_file()
        or os.getenv("PUBLIC_LOG_PRIVACY", "").strip() == "1"
    )
    if not public_mode:
        result = subprocess.run(args.command, check=False)
        return result.returncode

    result = subprocess.run(
        args.command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    elapsed = time.monotonic() - started_at
    if result.returncode:
        print(
            f"::error title=Fallo del componente::{args.label} termino "
            f"con codigo {result.returncode}. Revise el repositorio privado."
        )
        return result.returncode

    print(f"[runner] Componente completado en {elapsed:.1f} segundos.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
