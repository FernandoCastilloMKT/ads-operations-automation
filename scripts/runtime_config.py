"""Compatibility import for the shared configuration loaders."""
import sys
from pathlib import Path

scripts_dir = str(Path(__file__).resolve().parent)
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)
from shared import configuration

sys.modules[__name__] = configuration
