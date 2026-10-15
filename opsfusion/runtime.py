"""Explicit data root, separate from immutable deployment code."""
import os
from pathlib import Path


def data_root(fallback=None):
    value=os.environ.get('OPSFUSION_DATA_ROOT')
    if value:
        p=Path(value)
        if not p.is_absolute():raise ValueError('OPSFUSION_DATA_ROOT must be absolute')
        return p.resolve()
    return Path(fallback or Path.cwd()).resolve()
