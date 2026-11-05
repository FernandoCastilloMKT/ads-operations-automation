import argparse
import json
import re
import subprocess
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
    ".css", ".svg", ".xml", ".lock", ".ipynb", ".map", ".rst",
}
FORBIDDEN_NAMES = {
    ".env",
    ".clasprc.json",
    ".clasp.json",
    ".webapp-deployment.json",
    "config_clientes.json",
    "config_fichas_sem.json",
    "config_meta_ads_dinamizaciones.json",
    "config_linkedin_ads_dinamizaciones.json",
    "google-sheets-service-account.json",
    "google-workspace-token.json",
    "google-drive-backups-token.json",
    "config_account_alerts.json",
    "config_workbook_backups.json",
}
STATIC_PATTERNS = {
    "correo real": re.compile(
        r"\b[A-Z0-9._%+-]+@"
        r"(?!(?:example\.invalid|users\.noreply\.github\.com|group\.v\.calendar\.google\.com)\b)"
        r"[A-Z0-9.-]+\.[A-Z]{2,}\b",
        re.I,
    ),
    "ruta de usuario Windows": re.compile(r"(?i)\b[A-Z]:[\\/]Users[\\/][^\\/\s]+"),
    "token GitHub": re.compile(
        r"\b(?:github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{20,})\b"
    ),
    "clave API de Google": re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),
    "secreto OAuth de Google": re.compile(r"\bGOCSPX-[A-Za-z0-9_-]{20,}\b"),
    "refresh token de Google": re.compile(r"(?<!\w)1//[A-Za-z0-9_-]{30,}"),
    "clave API privada": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "clave AWS": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "cliente OAuth de Google": re.compile(
        r"\b\d{8,}-[a-z0-9_-]{16,}\.apps\.googleusercontent\.com\b",
        re.I,
    ),
    "token de acceso Meta": re.compile(r"\bEA[A-Za-z0-9]{60,}\b"),
    "token JWT": re.compile(
        r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\."
        r"[A-Za-z0-9_-]{10,}\b"
    ),
    "clave privada": re.compile(
        r"-----BEGIN [A-Z ]*"
        r"PRIVATE KEY-----"
    ),
    "ID publicitario de 10 digitos": re.compile(r"(?<![A-Za-z0-9])(?!0{10})(\d{10})(?![A-Za-z0-9])"),
    "ID publicitario con separadores": re.compile(r"(?<!\d)(?!000-000-0000)\d{3}-\d{3}-\d{4}(?!\d)"),
    "URL de Sheet real": re.compile(r"/spreadsheets/d/(?!EXAMPLE_)[A-Za-z0-9_-]{24,}"),
    "identificador de medicion real": re.compile(r"\b(?:GTM-[A-Z0-9]{6,}|G-[A-Z0-9]{10})\b"),
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
    parser.add_argument("--deny-term", action="append", default=[])
    parser.add_argument("--history", help="Revision Git o --all; requiere historial completo.")
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
            raise ValueError("Falta una referencia privada requerida para la auditoria.")
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
    return needles


def private_patterns(needles):
    return [re.compile(
        r"(?<![^\W_])" + r"[\s_.-]*".join(re.escape(p) for p in needle.split())
        + r"(?![^\W_])", re.I,
    ) for needle in needles if needle.strip()]


def text_findings(text, patterns):
    labels = {label for label, pattern in STATIC_PATTERNS.items() if pattern.search(text)}
    if any(pattern.search(text) for pattern in patterns):
        labels.add("dato presente en configuracion privada")
    return labels


def audit_history(root, revision, private_needles):
    def git(*args):
        return subprocess.check_output(
            ["git", "-C", str(root), *args], stderr=subprocess.PIPE,
        ).decode("utf-8", errors="replace")
    if Path(git("rev-parse", "--show-toplevel").strip()).resolve() != root.resolve():
        raise ValueError("La raiz auditada no es un checkout Git independiente.")
    if git("rev-parse", "--is-shallow-repository").strip() == "true":
        raise ValueError("Historial incompleto; descargarlo antes de auditar.")
    patterns = private_patterns(private_needles)
    findings = []
    for commit in git("rev-list", revision).split():
        patch = git("show", "--format=fuller", "--root", "-m", "--no-ext-diff",
                    "--no-textconv", "--no-color", commit)
        patch = patch.replace("<noreply" + "@" + "github.com>", "<github-system>")
        for label in text_findings(patch, patterns):
            findings.append((root / "history" / commit, label))
        names = git("ls-tree", "-r", "--name-only", commit).splitlines()
        for name in names:
            if forbidden_file(Path(name)):
                findings.append((root / "history" / commit, "archivo privado historico"))
    return sorted(set(findings))


def forbidden_file(path):
    name = path.name.lower()
    return (
        name in FORBIDDEN_NAMES
        or (name.startswith(".env.") and name != ".env.example")
        or bool({"credentials", ".secrets", ".ssh", ".openai", ".playwright-cli"} & {p.lower() for p in path.parts})
        or path.suffix.lower() in {".pem", ".key", ".p12", ".pfx", ".7z", ".zip", ".sqlite", ".har"}
    )


def iter_text_files(root):
    for path in root.rglob("*"):
        if (not path.is_file() and not path.is_symlink()) or ".git" in path.parts:
            continue
        if path.suffix.lower() in TEXT_SUFFIXES or path.name.startswith("."):
            yield path


def audit(root, private_needles):
    findings = []
    # Underscores delimit identifiers; letters inside ordinary words do not.
    patterns = private_patterns(private_needles)
    for path in root.rglob("*"):
        if (not path.is_file() and not path.is_symlink()) or ".git" in path.parts:
            continue
        relative_path = str(path.relative_to(root))
        if forbidden_file(path):
            findings.append((path, "archivo privado prohibido"))
        if path.is_symlink():
            findings.append((path, "enlace simbolico no permitido"))
        if any(pattern.search(relative_path) for pattern in patterns):
            findings.append((path, "dato privado en la ruta"))

    for path in iter_text_files(root):
        if path.is_symlink():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            findings.append((path, "texto no auditable como UTF-8"))
            continue
        for label in text_findings(text, patterns):
            findings.append((path, label))
    return sorted(set(findings), key=lambda item: (str(item[0]), item[1]))


def main():
    args = parse_args()
    root = args.root.resolve()
    needles = collect_private_needles(args.private_reference) | set(args.deny_term)
    findings = audit(root, needles)
    if args.history:
        findings.extend(audit_history(root, args.history, needles))
    if findings:
        for path, label in findings:
            print(f"{path.relative_to(root)}: {label}")
        print(f"Auditoria publica bloqueada: {len(findings)} hallazgos.")
        return 1
    print("Auditoria publica correcta: no se detectaron datos privados.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
