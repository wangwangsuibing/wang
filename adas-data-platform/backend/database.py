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
    # migration: add task_id to existing points table
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(points)").fetchall()]
    if "task_id" not in cols:
        conn.execute("ALTER TABLE points ADD COLUMN task_id INTEGER")
    conn.commit()
    conn.close()


def rows_to_dicts(rows):
    return [dict(r) for r in rows]
