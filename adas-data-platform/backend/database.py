"""SQLite database layer for the ADAS data collection platform."""
import sqlite3
import os
import sys

if getattr(sys, "frozen", False):  # PyInstaller exe: keep db next to exe
    DB_PATH = os.path.join(os.path.dirname(sys.executable), "platform.db")
else:
    DB_PATH = os.path.join(os.path.dirname(__file__), "platform.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS vehicles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    plate TEXT,
    status TEXT DEFAULT 'idle',          -- idle / collecting / offline
    lat REAL, lng REAL,                  -- WGS-84
    heading REAL DEFAULT 0,
    speed REAL DEFAULT 0,
    battery REAL DEFAULT 100,
    updated_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS points (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    lat REAL NOT NULL, lng REAL NOT NULL,
    type TEXT DEFAULT 'poi',             -- poi / event / obstacle / start / end
    task_id INTEGER,                     -- associated collection task
    note TEXT DEFAULT '',
    weather TEXT DEFAULT '',
    lighting TEXT DEFAULT '',
    road TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS paths (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    coords TEXT NOT NULL,                -- JSON [[lat,lng],...] WGS-84
    color TEXT DEFAULT '#2d8cf0',
    length_km REAL DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    vehicle_id INTEGER,
    path_id INTEGER,
    priority TEXT DEFAULT 'normal',      -- low / normal / high / urgent
    status TEXT DEFAULT 'pending',       -- pending / dispatched / running / done / cancelled
    note TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now','localtime')),
    dispatched_at TEXT,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS track_points (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id INTEGER NOT NULL,
    lat REAL NOT NULL, lng REAL NOT NULL,
    speed REAL DEFAULT 0,
    ts TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS geofences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    coords TEXT NOT NULL,                -- JSON polygon [[lat,lng],...]
    color TEXT DEFAULT '#ff9900',
    created_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    point_id INTEGER NOT NULL,
    filename TEXT NOT NULL,              -- stored filename
    orig_name TEXT NOT NULL,             -- original filename
    size INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS datasets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    task_id INTEGER,
    vehicle_id INTEGER,
    sensors TEXT DEFAULT '[]',           -- JSON ["camera","lidar",...]
    status TEXT DEFAULT 'uploading',     -- uploading / uploaded / qc_running / qc_passed / qc_failed / archived
    size_bytes INTEGER DEFAULT 0,
    duration_s REAL DEFAULT 0,
    tags TEXT DEFAULT '[]',              -- JSON ["雨天","路口",...]
    qc_score REAL,                       -- 0-100 quality score
    qc_report TEXT,                      -- JSON QC report
    note TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now','localtime')),
    uploaded_at TEXT,
    archived_at TEXT
);

CREATE TABLE IF NOT EXISTS dataset_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_id INTEGER NOT NULL,
    filename TEXT NOT NULL,              -- stored filename
    orig_name TEXT NOT NULL,
    category TEXT DEFAULT 'other',       -- camera / lidar / radar / gnss / can / log / other
    size INTEGER DEFAULT 0,
    sha256 TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS drivers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    phone TEXT DEFAULT '',
    status TEXT DEFAULT 'available',     -- available / on_task / off
    note TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS sensor_configs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    config TEXT DEFAULT '{}',            -- JSON {"cameras":6,"camera_fps":30,"lidar":true,...}
    note TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS campaigns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    sensor_config_id INTEGER,
    event_rules TEXT DEFAULT '[]',       -- JSON [{"trigger":"AEB","pre_s":10,"post_s":30}]
    note TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    target TEXT DEFAULT '',
    detail TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS dataset_consumers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_id INTEGER NOT NULL,
    consumer TEXT NOT NULL,              -- 标注 / 训练 / 仿真 / 外部交付
    note TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS upload_sessions (
    id TEXT PRIMARY KEY,                 -- uuid
    dataset_id INTEGER NOT NULL,
    orig_name TEXT NOT NULL,
    size INTEGER NOT NULL,
    chunk_size INTEGER NOT NULL,
    total_chunks INTEGER NOT NULL,
    received TEXT DEFAULT '[]',          -- JSON chunk index list
    status TEXT DEFAULT 'active',        -- active / done / aborted
    created_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id INTEGER,
    level TEXT DEFAULT 'warning',        -- info / warning / critical
    message TEXT NOT NULL,
    read INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
"""


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = get_conn()
    conn.executescript(SCHEMA)
    _ensure_columns(conn, "points", {"task_id": "INTEGER"})
    _ensure_columns(conn, "tasks", {
        "progress": "REAL DEFAULT 0",
        "driver_id": "INTEGER",
        "sensor_config_id": "INTEGER",
        "campaign_id": "INTEGER",
        "event_rules": "TEXT DEFAULT '[]'",
        "checklist_done": "INTEGER DEFAULT 0",
        "target_km": "REAL DEFAULT 0",
    })
    _ensure_columns(conn, "datasets", {
        "anonymized": "TEXT DEFAULT 'pending'",   # pending / running / done / not_required
        "priority": "TEXT DEFAULT 'normal'",      # low / normal / high
        "event_type": "TEXT DEFAULT ''",
        "upload_progress": "REAL DEFAULT 0",
    })
    conn.commit()
    conn.close()


def _ensure_columns(conn, table, cols: dict):
    existing = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    for name, decl in cols.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def rows_to_dicts(rows):
    return [dict(r) for r in rows]


import json as _json

DEFAULT_SETTINGS = {
    "qc_rules": {"drop_rate_max": 1.0, "sync_err_max_ms": 5.0, "pass_score": 60,
                 "camera_exposure_check": True, "lidar_density_check": True, "gps_loss_check": True},
    "retention_days": 90,
    "storage_warn_percent": 80,
}


def get_setting(key):
    conn = get_conn()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    if row:
        return _json.loads(row["value"])
    return DEFAULT_SETTINGS.get(key)


def set_setting(key, value):
    conn = get_conn()
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, _json.dumps(value, ensure_ascii=False)))
    conn.commit(); conn.close()


def log_audit(action, target="", detail=""):
    conn = get_conn()
    conn.execute("INSERT INTO audit_log (action, target, detail) VALUES (?,?,?)", (action, target, detail))
    conn.commit(); conn.close()
