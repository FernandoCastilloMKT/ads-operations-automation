import argparse
import json
import re
import sys
from pathlib import Path


TEXT_SUFFIXES = {
    ".example",
    ".gs",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".txt",
    ".toml",
    ".csv",
    ".sh",
    ".yaml",
    ".yml",
}
FORBIDDEN_NAMES = {
    ".env",
    ".clasp.json",
    ".webapp-deployment.json",
    "config_clientes.json",
    "config_fichas_sem.json",
    "config_meta_ads_dinamizaciones.json",
    "config_linkedin_ads_dinamizaciones.json",
    "google-sheets-service-account.json",
    "google-workspace-token.json",
}
PRIVATE_BRAND_PARTS = ("vision", "click")
PRIVATE_OWNER_PARTS = ("fernand", *PRIVATE_BRAND_PARTS)
PRIVATE_BRAND_TERMS = {
    "".join(PRIVATE_BRAND_PARTS),
    "".join(PRIVATE_OWNER_PARTS),
}


def joined_identity_pattern(parts):
    separator = r"[\s_.-]*"
    return re.compile(separator.join(re.escape(part) for part in parts), re.I)


PRIVATE_IDENTITY_PATTERNS = (
    joined_identity_pattern(PRIVATE_BRAND_PARTS),
    joined_identity_pattern(PRIVATE_OWNER_PARTS),
)
STATIC_PATTERNS = {
    "identidad interna": PRIVATE_IDENTITY_PATTERNS[0],
    "correo real": re.compile(
        r"\b[A-Z0-9._%+-]+@"
        r"(?!(?:example\.invalid|group\.v\.calendar\.google\.com)\b)"
        r"[A-Z0-9.-]+\.[A-Z]{2,}\b",
        re.I,
    ),
    "ruta de usuario Windows": re.compile(r"(?i)\bC:\\Users\\[^\\\s]+"),
    "token GitHub": re.compile(r"\b(?:github_pat_|gh[pousr]_[A-Za-z0-9_]+)"),
    "clave privada": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "ID publicitario de 10 digitos": re.compile(r"(?<!\d)(?!0{10})(\d{10})(?!\d)"),
    "ID de Sheet literal": re.compile(
        r"(?i)\b[A-Z_]*(?:SPREADSHEET|SHEET)_ID\s*=\s*['\"]"
        r"(?!EXAMPLE_)([A-Za-z0-9_-]{24,})['\"]"
    ),
}


def parse_args():
    parser = argparse.ArgumentParser(
        description="Impide publicar datos internos o secretos."
    )
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument(
        "--private-reference",
        action="append",
        type=Path,
        default=[],
    )
    return parser.parse_args()


def collect_private_needles(reference_paths):
    needles = set()
    for path in reference_paths:
        if not path.is_file():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if path.name == "config_clientes.json":
            for client in data.get("clientes", []):
                for key in (
                    "nombre",
                    "mcc_id",
                    "spreadsheet_id",
                ):
                    value = str(client.get(key, "")).strip()
                    if len(value) >= 4:
                        needles.add(value)
                for value in client.get("sub_mcc_ids", []):
                    needles.add(str(value))
                for source in client.get("manager_sources", []):
                    for key in ("nombre", "mcc_id"):
                        value = str(source.get(key, "")).strip()
                        if len(value) >= 4:
                            needles.add(value)
        elif path.name == "config_fichas_sem.json":
            for key in (
                "control_sem_spreadsheet_id",
                "calendar_email_recipient",
            ):
                value = str(data.get(key, "")).strip()
                if len(value) >= 4:
                    needles.add(value)
            for client in data.get("clients", {}).values():
                for key in (
                    "nombre",
                    "worksheet_name",
                    "mcc_id",
                    "google_ads_customer_id",
                    "microsoft_ads_account_id",
                    "microsoft_ads_customer_id",
                ):
                    value = str(client.get(key, "")).strip()
                    if len(value) >= 4:
                        needles.add(value)
                for key in ("aliases", "google_ads_customer_ids"):
                    for value in client.get(key, []):
                        if len(str(value)) >= 4:
                            needles.add(str(value))
        elif path.name == "config_meta_ads_dinamizaciones.json":
            spreadsheet_id = str(data.get("spreadsheet_id", "")).strip()
            if len(spreadsheet_id) >= 4:
                needles.add(spreadsheet_id)
            for client in data.get("clients", {}).values():
                for key in (
                    "nombre",
                    "worksheet_name",
                    "meta_ad_account_id",
                ):
                    value = str(client.get(key, "")).strip()
                    if len(value) >= 4:
                        needles.add(value)
                for value in client.get("campaign_name_contains", []):
                    if len(str(value)) >= 4:
                        needles.add(str(value))
        elif path.name == "config_linkedin_ads_dinamizaciones.json":
            spreadsheet_id = str(data.get("spreadsheet_id", "")).strip()
            if len(spreadsheet_id) >= 4:
                needles.add(spreadsheet_id)
            client = data.get("client", {})
            for key in (
                "nombre",
                "worksheet_name",
                "linkedin_ad_account_id",
            ):
                value = str(client.get(key, "")).strip()
                if len(value) >= 4:
                    needles.add(value)
            for value in client.get("special_campaign_names", []):
                if len(str(value)) >= 4:
                    needles.add(str(value))
    return {
        needle
        for needle in needles
        if needle.casefold() not in PRIVATE_BRAND_TERMS
    }


def iter_text_files(root):
    for path in root.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        if path.suffix.lower() in TEXT_SUFFIXES or path.name.startswith("."):
            yield path


def audit(root, private_needles):
    findings = []
    for path in root.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        relative_path = str(path.relative_to(root))
        if path.name in FORBIDDEN_NAMES or "credentials" in path.parts:
            findings.append((path, "archivo privado prohibido"))
        if any(pattern.search(relative_path) for pattern in PRIVATE_IDENTITY_PATTERNS):
            findings.append((path, "identidad interna en la ruta"))

    for path in iter_text_files(root):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for label, pattern in STATIC_PATTERNS.items():
            if (
                path.name == "audit_public_repository.py"
                and label in {"token GitHub", "clave privada"}
            ):
                continue
            if pattern.search(text):
                findings.append((path, label))
        if any(needle in text for needle in private_needles):
            findings.append((path, "dato presente en configuracion privada"))
    return sorted(set(findings), key=lambda item: (str(item[0]), item[1]))


def main():
    args = parse_args()
    root = args.root.resolve()
    findings = audit(
        root,
        collect_private_needles(args.private_reference),
    )
    if findings:
        for path, label in findings:
            print(f"{path.relative_to(root)}: {label}")
        print(f"Auditoria publica bloqueada: {len(findings)} hallazgos.")
        return 1
    print("Auditoria publica correcta: no se detectaron datos privados.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
