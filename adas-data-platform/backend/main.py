"""ADAS Data Collection Platform — FastAPI backend."""
import csv
import io
import json
import math
import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from database import init_db, get_conn, rows_to_dicts
from models import VehicleIn, PointIn, PathIn, TaskIn, GeofenceIn, StatusUpdate
from seed import seed_if_empty
from simulator import simulator

if getattr(sys, "frozen", False):  # PyInstaller exe
    FRONTEND = os.path.join(sys._MEIPASS, "frontend")
    UPLOAD_DIR = os.path.join(os.path.dirname(sys.executable), "uploads")
else:
    FRONTEND = os.path.join(os.path.dirname(__file__), "..", "frontend")
    UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    seed_if_empty()
    simulator.start()
    yield
    simulator.stop()


app = FastAPI(title="ADAS 数据采集平台", lifespan=lifespan)


# ---------- Vehicles ----------
@app.get("/api/vehicles")
def list_vehicles():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM vehicles ORDER BY id").fetchall()
    conn.close()
    return rows_to_dicts(rows)


@app.post("/api/vehicles")
def create_vehicle(v: VehicleIn):
    conn = get_conn()
    cur = conn.execute("INSERT INTO vehicles (name, plate, lat, lng) VALUES (?,?,?,?)",
                       (v.name, v.plate, v.lat, v.lng))
    conn.commit(); conn.close()
    return {"id": cur.lastrowid}


@app.put("/api/vehicles/{vid}/status")
def set_vehicle_status(vid: int, s: StatusUpdate):
    conn = get_conn()
    conn.execute("UPDATE vehicles SET status=? WHERE id=?", (s.status, vid))
    conn.commit(); conn.close()
    return {"ok": True}


# ---------- Points ----------
@app.get("/api/points")
def list_points(task_id: int | None = None):
    conn = get_conn()
    q = """SELECT p.*, t.name AS task_name FROM points p
           LEFT JOIN tasks t ON p.task_id = t.id"""
    args = ()
    if task_id is not None:
        q += " WHERE p.task_id = ?"
        args = (task_id,)
    rows = conn.execute(q + " ORDER BY p.id DESC", args).fetchall()
    conn.close()
    return rows_to_dicts(rows)


@app.post("/api/points")
def create_point(p: PointIn):
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO points (name, lat, lng, type, task_id, note, weather, lighting, road) VALUES (?,?,?,?,?,?,?,?,?)",
        (p.name, p.lat, p.lng, p.type, p.task_id, p.note, p.weather, p.lighting, p.road))
    conn.commit(); conn.close()
    return {"id": cur.lastrowid}


@app.delete("/api/points/{pid}")
def delete_point(pid: int):
    conn = get_conn()
    atts = conn.execute("SELECT filename FROM attachments WHERE point_id=?", (pid,)).fetchall()
    for a in atts:
        try:
            os.remove(os.path.join(UPLOAD_DIR, a["filename"]))
        except OSError:
            pass
    conn.execute("DELETE FROM attachments WHERE point_id=?", (pid,))
    conn.execute("DELETE FROM points WHERE id=?", (pid,))
    conn.commit(); conn.close()
    return {"ok": True}


# ---------- Paths ----------
@app.get("/api/paths")
def list_paths():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM paths ORDER BY id DESC").fetchall()
    conn.close()
    out = rows_to_dicts(rows)
    for r in out:
        r["coords"] = json.loads(r["coords"])
    return out


@app.post("/api/paths")
def create_path(p: PathIn):
    if len(p.coords) < 2:
        raise HTTPException(400, "路径至少需要 2 个点")
    total = 0.0
    for a, b in zip(p.coords, p.coords[1:]):
        dlat = (b[0] - a[0]) * 111.0
        dlng = (b[1] - a[1]) * 111.0 * math.cos(math.radians(a[0]))
        total += math.hypot(dlat, dlng)
    conn = get_conn()
    cur = conn.execute("INSERT INTO paths (name, coords, color, length_km) VALUES (?,?,?,?)",
                       (p.name, json.dumps(p.coords), p.color, round(total, 2)))
    conn.commit(); conn.close()
    return {"id": cur.lastrowid, "length_km": round(total, 2)}


@app.delete("/api/paths/{pid}")
def delete_path(pid: int):
    conn = get_conn()
    conn.execute("DELETE FROM paths WHERE id=?", (pid,))
    conn.commit(); conn.close()
    return {"ok": True}


# ---------- Tasks ----------
@app.get("/api/tasks")
def list_tasks():
    conn = get_conn()
    rows = conn.execute(
        """SELECT t.*, v.name AS vehicle_name, p.name AS path_name
           FROM tasks t LEFT JOIN vehicles v ON t.vehicle_id=v.id
           LEFT JOIN paths p ON t.path_id=p.id ORDER BY t.id DESC""").fetchall()
    conn.close()
    return rows_to_dicts(rows)


@app.post("/api/tasks")
def create_task(t: TaskIn):
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO tasks (name, vehicle_id, path_id, priority, note) VALUES (?,?,?,?,?)",
        (t.name, t.vehicle_id, t.path_id, t.priority, t.note))
    conn.commit(); conn.close()
    return {"id": cur.lastrowid}


@app.post("/api/tasks/{tid}/dispatch")
def dispatch_task(tid: int):
    conn = get_conn()
    task = conn.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
    if not task:
        conn.close(); raise HTTPException(404, "任务不存在")
    if not task["vehicle_id"]:
        # auto-assign an idle vehicle
        v = conn.execute("SELECT id FROM vehicles WHERE status='idle' ORDER BY battery DESC LIMIT 1").fetchone()
        if not v:
            conn.close(); raise HTTPException(400, "没有空闲车辆可分配")
        conn.execute("UPDATE tasks SET vehicle_id=? WHERE id=?", (v["id"], tid))
    conn.execute("UPDATE tasks SET status='running', dispatched_at=datetime('now','localtime') WHERE id=?", (tid,))
    vid = conn.execute("SELECT vehicle_id FROM tasks WHERE id=?", (tid,)).fetchone()["vehicle_id"]
    conn.execute("UPDATE vehicles SET status='collecting' WHERE id=?", (vid,))
    conn.execute("INSERT INTO alerts (vehicle_id, level, message) VALUES (?,?,?)",
                 (vid, "info", f"任务 #{tid} 已下发"))
    conn.commit(); conn.close()
    return {"ok": True}


@app.post("/api/tasks/{tid}/cancel")
def cancel_task(tid: int):
    conn = get_conn()
    task = conn.execute("SELECT vehicle_id FROM tasks WHERE id=?", (tid,)).fetchone()
    conn.execute("UPDATE tasks SET status='cancelled' WHERE id=?", (tid,))
    if task and task["vehicle_id"]:
        conn.execute("UPDATE vehicles SET status='idle', speed=0 WHERE id=?", (task["vehicle_id"],))
    conn.commit(); conn.close()
    return {"ok": True}


# ---------- Tracks / heatmap / replay ----------
@app.get("/api/heatmap")
def heatmap_data(limit: int = 3000):
    conn = get_conn()
    rows = conn.execute("SELECT lat, lng FROM track_points ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [[r["lat"], r["lng"], 0.6] for r in rows]


@app.get("/api/tracks/{vid}")
def vehicle_track(vid: int, limit: int = 500):
    conn = get_conn()
    rows = conn.execute(
        "SELECT lat, lng, speed, ts FROM track_points WHERE vehicle_id=? ORDER BY id DESC LIMIT ?",
        (vid, limit)).fetchall()
    conn.close()
    return rows_to_dicts(rows)[::-1]


# ---------- Geofences ----------
@app.get("/api/geofences")
def list_geofences():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM geofences ORDER BY id").fetchall()
    conn.close()
    out = rows_to_dicts(rows)
    for r in out:
        r["coords"] = json.loads(r["coords"])
    return out


@app.post("/api/geofences")
def create_geofence(g: GeofenceIn):
    if len(g.coords) < 3:
        raise HTTPException(400, "围栏至少需要 3 个点")
    conn = get_conn()
    cur = conn.execute("INSERT INTO geofences (name, coords, color) VALUES (?,?,?)",
                       (g.name, json.dumps(g.coords), g.color))
    conn.commit(); conn.close()
    return {"id": cur.lastrowid}


@app.delete("/api/geofences/{gid}")
def delete_geofence(gid: int):
    conn = get_conn()
    conn.execute("DELETE FROM geofences WHERE id=?", (gid,))
    conn.commit(); conn.close()
    return {"ok": True}


# ---------- Attachments ----------
MAX_UPLOAD = 100 * 1024 * 1024  # 100MB


@app.post("/api/points/{pid}/attachments")
async def upload_attachment(pid: int, file: UploadFile = File(...)):
    conn = get_conn()
    if not conn.execute("SELECT 1 FROM points WHERE id=?", (pid,)).fetchone():
        conn.close(); raise HTTPException(404, "采集点不存在")
    data = await file.read()
    if len(data) > MAX_UPLOAD:
        conn.close(); raise HTTPException(400, "文件过大（限 100MB）")
    import uuid
    ext = os.path.splitext(file.filename or "")[1]
    stored = f"{uuid.uuid4().hex}{ext}"
    with open(os.path.join(UPLOAD_DIR, stored), "wb") as f:
        f.write(data)
    cur = conn.execute(
        "INSERT INTO attachments (point_id, filename, orig_name, size) VALUES (?,?,?,?)",
        (pid, stored, file.filename or stored, len(data)))
    conn.commit(); conn.close()
    return {"id": cur.lastrowid, "size": len(data)}


@app.get("/api/points/{pid}/attachments")
def list_attachments(pid: int):
    conn = get_conn()
    rows = conn.execute("SELECT * FROM attachments WHERE point_id=? ORDER BY id DESC", (pid,)).fetchall()
    conn.close()
    return rows_to_dicts(rows)


@app.get("/api/attachments/{aid}/download")
def download_attachment(aid: int):
    conn = get_conn()
    row = conn.execute("SELECT * FROM attachments WHERE id=?", (aid,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "附件不存在")
    path = os.path.join(UPLOAD_DIR, row["filename"])
    if not os.path.exists(path):
        raise HTTPException(404, "文件已丢失")
    return FileResponse(path, filename=row["orig_name"])


@app.delete("/api/attachments/{aid}")
def delete_attachment(aid: int):
    conn = get_conn()
    row = conn.execute("SELECT filename FROM attachments WHERE id=?", (aid,)).fetchone()
    if row:
        conn.execute("DELETE FROM attachments WHERE id=?", (aid,))
        conn.commit()
        try:
            os.remove(os.path.join(UPLOAD_DIR, row["filename"]))
        except OSError:
            pass
    conn.close()
    return {"ok": True}


# ---------- Alerts ----------
@app.get("/api/alerts")
def list_alerts(unread_only: bool = False):
    conn = get_conn()
    q = "SELECT a.*, v.name AS vehicle_name FROM alerts a LEFT JOIN vehicles v ON a.vehicle_id=v.id"
    if unread_only:
        q += " WHERE a.read=0"
    q += " ORDER BY a.id DESC LIMIT 50"
    rows = conn.execute(q).fetchall()
    conn.close()
    return rows_to_dicts(rows)


@app.post("/api/alerts/read_all")
def mark_alerts_read():
    conn = get_conn()
    conn.execute("UPDATE alerts SET read=1")
    conn.commit(); conn.close()
    return {"ok": True}


# ---------- Stats ----------
@app.get("/api/stats")
def stats():
    conn = get_conn()
    def one(q):
        return conn.execute(q).fetchone()[0]
    s = {
        "vehicles_total": one("SELECT COUNT(*) FROM vehicles"),
        "vehicles_collecting": one("SELECT COUNT(*) FROM vehicles WHERE status='collecting'"),
        "points_total": one("SELECT COUNT(*) FROM points"),
        "paths_total": one("SELECT COUNT(*) FROM paths"),
        "paths_km": one("SELECT COALESCE(SUM(length_km),0) FROM paths"),
        "tasks_total": one("SELECT COUNT(*) FROM tasks"),
        "tasks_running": one("SELECT COUNT(*) FROM tasks WHERE status='running'"),
        "tasks_done": one("SELECT COUNT(*) FROM tasks WHERE status='done'"),
        "track_points": one("SELECT COUNT(*) FROM track_points"),
        "alerts_unread": one("SELECT COUNT(*) FROM alerts WHERE read=0"),
    }
    total = s["tasks_total"]
    s["task_done_rate"] = round(s["tasks_done"] * 100.0 / total, 1) if total else 0
    # rough coverage: distinct 0.005-degree grid cells with track points
    s["coverage_cells"] = one(
        "SELECT COUNT(DISTINCT CAST(lat/0.005 AS INT) || ',' || CAST(lng/0.005 AS INT)) FROM track_points")
    conn.close()
    return s


# ---------- Export ----------
@app.get("/api/export/points.csv")
def export_points_csv():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM points").fetchall()
    conn.close()
    buf = io.StringIO()
    if rows:
        w = csv.DictWriter(buf, fieldnames=rows[0].keys())
        w.writeheader()
        for r in rows:
            w.writerow(dict(r))
    return StreamingResponse(io.BytesIO(buf.getvalue().encode("utf-8-sig")),
                             media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=points.csv"})


@app.get("/api/export/geojson")
def export_geojson():
    conn = get_conn()
    points = conn.execute("SELECT * FROM points").fetchall()
    paths = conn.execute("SELECT * FROM paths").fetchall()
    conn.close()
    features = []
    for p in points:
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [p["lng"], p["lat"]]},
            "properties": {"name": p["name"], "type": p["type"], "task_id": p["task_id"], "note": p["note"]},
        })
    for pa in paths:
        coords = json.loads(pa["coords"])
        features.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": [[c[1], c[0]] for c in coords]},
            "properties": {"name": pa["name"], "length_km": pa["length_km"]},
        })
    return JSONResponse({"type": "FeatureCollection", "features": features},
                        headers={"Content-Disposition": "attachment; filename=export.geojson"})


# ---------- Frontend ----------
app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")


if __name__ == "__main__":
    import threading
    import webbrowser
    import uvicorn
    threading.Timer(1.5, lambda: webbrowser.open("http://127.0.0.1:8080")).start()
    uvicorn.run(app, host="127.0.0.1", port=8080)
