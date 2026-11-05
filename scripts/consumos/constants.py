"""Static application constants; importing this module performs no I/O."""



MAX_GOOGLE_ADS_RETRIES = 3

RETRY_DELAY_SECONDS = 5

DEFAULT_PARALLEL_WORKERS = 10

DEFAULT_QUICK_LOOKBACK_DAYS = 7

EURO_NUMBER_FORMAT_PATTERN = "#,##0.00\\ [$€-1]"

ACCOUNT_STATUS_LABELS = {
    "ENABLED": "",
    "SUSPENDED": "Suspendida",
    "CANCELED": "Cancelada",
    "CANCELLED": "Cancelada",
    "CLOSED": "Cerrada",
    "PAUSED": "Pausada",
    "REMOVED": "Eliminada",
    "DISABLED": "Desactivada",
    "DRAFT": "Borrador",
    "HIDDEN": "Oculta",
    "FINALIZED": "Finalizada",
    "UNKNOWN": "Estado desconocido",
    "UNSPECIFIED": "Sin especificar",
}

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
