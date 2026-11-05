"""Stable repository paths, independent of application module depth."""

from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
