from __future__ import annotations

import sqlite3

import app.config as config


def db_path():
    """Resolved per call so tests can point DATA_DIR at a scratch directory."""
    data_dir = config.DATA_DIR
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / config.DB_FILENAME


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn
