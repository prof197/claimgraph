"""Where the gitignored data lives: run outputs and local working files.

Every checkout of the repository can share one copy by pointing CLAIMGRAPH_DATA at it.
"""

import os
from pathlib import Path

DATA_ENV = "CLAIMGRAPH_DATA"


def data_root() -> Path:
    """The directory holding runs/ and local-data/: CLAIMGRAPH_DATA, else the working directory."""
    value = os.environ.get(DATA_ENV, "")
    if not value:
        return Path()
    root = Path(value)
    if not root.is_absolute():
        # A relative path would point somewhere different from each checkout.
        raise ValueError(f"{DATA_ENV} must be an absolute path, not {value!r}")
    return root


def runs_dir() -> Path:
    return data_root() / "runs"


def local_dir() -> Path:
    """Local working files: review pages, their notes and label backups. Never committed."""
    return data_root() / "local-data"
