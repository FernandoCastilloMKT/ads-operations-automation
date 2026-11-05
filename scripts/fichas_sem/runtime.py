"""SEM startup and configured state. Imported only by operational modules."""

import sys
import threading

from dotenv import load_dotenv

from runtime_config import load_sem_runtime_config, normalize_config_key
from shared.paths import REPOSITORY_ROOT as BASE_DIR
from .constants import MONTHLY_RENEWAL_NOTE_PREFIX

env_path = BASE_DIR / ".env"
load_dotenv(env_path, override=True)
sheets_credentials_path = BASE_DIR / "credentials" / "google-sheets-service-account.json"
vendor_path = BASE_DIR / ".vendor"
if vendor_path.exists():
    sys.path.insert(0, str(vendor_path))

try:
    import truststore

    if hasattr(truststore, "inject_into_ssl"):
        truststore.inject_into_ssl()
except ImportError:
    pass

_google_ads_thread_local = threading.local()
SEM_RUNTIME_CONFIG = load_sem_runtime_config()
CONTROL_SEM_SPREADSHEET_ID = SEM_RUNTIME_CONFIG["control_sem_spreadsheet_id"]
VISTA_GLOBAL_WORKSHEET_NAME = SEM_RUNTIME_CONFIG.get(
    "control_sem_worksheet_name", "Vista Global"
)
SEM_CLIENTS = SEM_RUNTIME_CONFIG["clients"]
SEM_ONLY_MCC_CONFIGS = SEM_RUNTIME_CONFIG.get("sem_only_mcc_configs", {})
EXCLUDED_SEM_CLIENTS = {
    normalize_config_key(value)
    for value in SEM_RUNTIME_CONFIG.get("excluded_sem_clients", [])
}
MONTHLY_RENEWAL_NOTE_PREFIXES = (
    MONTHLY_RENEWAL_NOTE_PREFIX,
    *SEM_RUNTIME_CONFIG.get("legacy_renewal_note_prefixes", []),
)
