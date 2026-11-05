"""Consumption startup and state shared within one execution."""

import logging
import os
import sys

from dotenv import load_dotenv

from shared.paths import REPOSITORY_ROOT as BASE_DIR

env_path = BASE_DIR / ".env"
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

DRY_RUN = False
FAILED_ACCOUNT_QUERIES = []
ACCOUNT_METADATA = {}
load_dotenv(env_path, override=True)
logging.getLogger("google.ads.googleads").setLevel(logging.CRITICAL)
developer_token = os.getenv("GOOGLE_ADS_DEVELOPER_TOKEN")
client_id = os.getenv("GOOGLE_ADS_CLIENT_ID")
client_secret = os.getenv("GOOGLE_ADS_CLIENT_SECRET")
refresh_token = os.getenv("GOOGLE_ADS_REFRESH_TOKEN")
