"""Backward-compatible SEM entry point; implementation lives in fichas_sem."""
import sys
from pathlib import Path

scripts_dir = str(Path(__file__).resolve().parent)
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)
from fichas_sem import service

if __name__ == "__main__":
    service.main()
else:
    sys.modules[__name__] = service
