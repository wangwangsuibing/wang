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

from database import init_db, get_conn, rows_to_dicts, get_setting, set_setting, log_audit
from models import (VehicleIn, PointIn, PathIn, TaskIn, GeofenceIn, StatusUpdate,
                    DriverIn, SensorConfigIn, CampaignIn)
from seed import seed_if_empty
from simulator import simulator
import datasets as datasets_mod

if getattr(sys, "frozen", False):  # PyInstaller exe
    FRONTEND = os.path.join(sys._MEIPASS, "frontend")
    UPLOAD_DIR = os.path.join(os.path.dirname(sys.executable), "uploads")
else:
    FRONTEND = os.path.join(os.path.dirname(__file__), "..", "frontend")
    UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)
DATASET_DIR = os.path.join(UPLOAD_DIR, "datasets")
os.makedirs(DATASET_DIR, exist_ok=True)
datasets_mod.DATASET_DIR = DATASET_DIR


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    seed_if_empty()
    simulator.start()
    yield
    simulator.stop()


app = FastAPI(title="ADAS 数据采集平台", lifespan=lifespan)
app.include_router(datasets_mod.router)


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
        """SELECT t.*, v.name AS vehicle_name, p.name AS path_name,
                  d.name AS driver_name, sc.name AS sensor_config_name, c.name AS campaign_name
           FROM tasks t LEFT JOIN vehicles v ON t.vehicle_id=v.id
           LEFT JOIN paths p ON t.path_id=p.id
           LEFT JOIN drivers d ON t.driver_id=d.id
           LEFT JOIN sensor_configs sc ON t.sensor_config_id=sc.id
           LEFT JOIN campaigns c ON t.campaign_id=c.id ORDER BY t.id DESC""").fetchall()
    conn.close()
    out = rows_to_dicts(rows)
    for r in out:
        r["event_rules"] = json.loads(r.get("event_rules") or "[]")
    return out


@app.post("/api/tasks")
def create_task(t: TaskIn):
    conn = get_conn()
    cur = conn.execute(
        """INSERT INTO tasks (name, vehicle_id, path_id, priority, note, driver_id,
           sensor_config_id, campaign_id, event_rules, target_km) VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (t.name, t.vehicle_id, t.path_id, t.priority, t.note, t.driver_id,
         t.sensor_config_id, t.campaign_id, json.dumps(t.event_rules, ensure_ascii=False), t.target_km))
    conn.commit(); conn.close()
    log_audit("task.create", f"task#{cur.lastrowid}", t.name)
    return {"id": cur.lastrowid}


CHECKLIST_ITEMS = ["传感器标定有效期确认", "相机镜头清洁", "激光雷达自检通过", "GNSS 天线连接", "存储剩余空间 ≥ 500GB", "时间同步源正常"]


@app.get("/api/tasks/checklist_template")
def checklist_template():
    return CHECKLIST_ITEMS


@app.post("/api/tasks/{tid}/checklist")
def confirm_checklist(tid: int):
    conn = get_conn()
    if not conn.execute("SELECT 1 FROM tasks WHERE id=?", (tid,)).fetchone():
        conn.close(); raise HTTPException(404, "任务不存在")
    conn.execute("UPDATE tasks SET checklist_done=1 WHERE id=?", (tid,))
    conn.commit(); conn.close()
    log_audit("task.checklist", f"task#{tid}", "出车检查单确认")
    return {"ok": True}


@app.post("/api/tasks/{tid}/dispatch")
def dispatch_task(tid: int):
    conn = get_conn()
    task = conn.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
    if not task:
        conn.close(); raise HTTPException(404, "任务不存在")
    if not task["checklist_done"]:
        conn.close(); raise HTTPException(400, "请先完成出车检查单确认")
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
    if task["driver_id"]:
        conn.execute("UPDATE drivers SET status='on_task' WHERE id=?", (task["driver_id"],))
    conn.commit(); conn.close()
    log_audit("task.dispatch", f"task#{tid}")
    return {"ok": True}


@app.post("/api/tasks/{tid}/cancel")
def cancel_task(tid: int):
    conn = get_conn()
    task = conn.execute("SELECT vehicle_id, driver_id FROM tasks WHERE id=?", (tid,)).fetchone()
    conn.execute("UPDATE tasks SET status='cancelled' WHERE id=?", (tid,))
    if task and task["vehicle_id"]:
        conn.execute("UPDATE vehicles SET status='idle', speed=0 WHERE id=?", (task["vehicle_id"],))
    if task and task["driver_id"]:
        conn.execute("UPDATE drivers SET status='available' WHERE id=?", (task["driver_id"],))
    conn.commit(); conn.close()
    log_audit("task.cancel", f"task#{tid}")
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
        "datasets_total": one("SELECT COUNT(*) FROM datasets"),
        "datasets_qc_passed": one("SELECT COUNT(*) FROM datasets WHERE status='qc_passed'"),
        "datasets_bytes": one("SELECT COALESCE(SUM(size_bytes),0) FROM datasets"),
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


# ---------- Drivers (采集员) ----------
@app.get("/api/drivers")
def list_drivers():
    conn = get_conn()
    rows = conn.execute(
        """SELECT d.*, (SELECT COUNT(*) FROM tasks t WHERE t.driver_id=d.id AND t.status='done') AS tasks_done
           FROM drivers d ORDER BY d.id""").fetchall()
    conn.close()
    return rows_to_dicts(rows)


@app.post("/api/drivers")
def create_driver(d: DriverIn):
    conn = get_conn()
    cur = conn.execute("INSERT INTO drivers (name, phone, note) VALUES (?,?,?)", (d.name, d.phone, d.note))
    conn.commit(); conn.close()
    log_audit("driver.create", f"driver#{cur.lastrowid}", d.name)
    return {"id": cur.lastrowid}


@app.delete("/api/drivers/{did}")
def delete_driver(did: int):
    conn = get_conn()
    conn.execute("DELETE FROM drivers WHERE id=?", (did,))
    conn.commit(); conn.close()
    log_audit("driver.delete", f"driver#{did}")
    return {"ok": True}


# ---------- Sensor configs (传感器配置方案) ----------
@app.get("/api/sensor_configs")
def list_sensor_configs():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM sensor_configs ORDER BY id").fetchall()
    conn.close()
    out = rows_to_dicts(rows)
    for r in out:
        r["config"] = json.loads(r["config"] or "{}")
    return out


@app.post("/api/sensor_configs")
def create_sensor_config(s: SensorConfigIn):
    conn = get_conn()
    cur = conn.execute("INSERT INTO sensor_configs (name, config, note) VALUES (?,?,?)",
                       (s.name, json.dumps(s.config, ensure_ascii=False), s.note))
    conn.commit(); conn.close()
    log_audit("sensor_config.create", f"sc#{cur.lastrowid}", s.name)
    return {"id": cur.lastrowid}


@app.delete("/api/sensor_configs/{sid}")
def delete_sensor_config(sid: int):
    conn = get_conn()
    conn.execute("DELETE FROM sensor_configs WHERE id=?", (sid,))
    conn.commit(); conn.close()
    return {"ok": True}


# ---------- Campaigns (采集活动: 批量任务) ----------
@app.get("/api/campaigns")
def list_campaigns():
    conn = get_conn()
    rows = conn.execute(
        """SELECT c.*, sc.name AS sensor_config_name,
                  (SELECT COUNT(*) FROM tasks t WHERE t.campaign_id=c.id) AS task_count,
                  (SELECT COUNT(*) FROM tasks t WHERE t.campaign_id=c.id AND t.status='done') AS task_done
           FROM campaigns c LEFT JOIN sensor_configs sc ON c.sensor_config_id=sc.id
           ORDER BY c.id DESC""").fetchall()
    conn.close()
    out = rows_to_dicts(rows)
    for r in out:
        r["event_rules"] = json.loads(r["event_rules"] or "[]")
    return out


@app.post("/api/campaigns")
def create_campaign(c: CampaignIn):
    conn = get_conn()
    cur = conn.execute("INSERT INTO campaigns (name, sensor_config_id, event_rules, note) VALUES (?,?,?,?)",
                       (c.name, c.sensor_config_id, json.dumps(c.event_rules, ensure_ascii=False), c.note))
    cid = cur.lastrowid
    created = []
    for vid in c.vehicle_ids:
        t = conn.execute(
            """INSERT INTO tasks (name, vehicle_id, path_id, priority, campaign_id,
               sensor_config_id, event_rules) VALUES (?,?,?,?,?,?,?)""",
            (f"{c.name}-车辆{vid}", vid, c.path_id, c.priority, cid,
             c.sensor_config_id, json.dumps(c.event_rules, ensure_ascii=False)))
        created.append(t.lastrowid)
    conn.commit(); conn.close()
    log_audit("campaign.create", f"campaign#{cid}", f"{c.name}，批量生成 {len(created)} 个任务")
    return {"id": cid, "task_ids": created}


# ---------- Audit log ----------
@app.get("/api/audit")
def list_audit(limit: int = 100):
    conn = get_conn()
    rows = conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return rows_to_dicts(rows)


# ---------- Storage watermark ----------
@app.get("/api/storage")
def storage_status():
    import shutil
    du = shutil.disk_usage(UPLOAD_DIR)
    upload_bytes = sum(f.stat().st_size for f in __import__("pathlib").Path(UPLOAD_DIR).rglob("*") if f.is_file())
    warn = get_setting("storage_warn_percent")
    used_pct = round(du.used * 100.0 / du.total, 1)
    return {"disk_total": du.total, "disk_used": du.used, "disk_free": du.free,
            "used_percent": used_pct, "upload_bytes": upload_bytes,
            "warn_percent": warn, "warning": used_pct >= warn}


# ---------- Reports (趋势报表) ----------
@app.get("/api/reports/daily")
def daily_report(days: int = 14):
    conn = get_conn()
    def series(q):
        return {r["d"]: r["v"] for r in conn.execute(q, (f"-{days} day",)).fetchall()}
    km = series("""SELECT DATE(ts) d, COUNT(*)*0.03 v FROM track_points
                   WHERE DATE(ts) >= DATE('now','localtime', ?) GROUP BY DATE(ts)""")
    data = series("""SELECT DATE(created_at) d, COALESCE(SUM(size_bytes),0) v FROM datasets
                     WHERE DATE(created_at) >= DATE('now','localtime', ?) GROUP BY DATE(created_at)""")
    qc_pass = series("""SELECT DATE(created_at) d, COUNT(*) v FROM datasets
                        WHERE status='qc_passed' AND DATE(created_at) >= DATE('now','localtime', ?) GROUP BY DATE(created_at)""")
    qc_total = series("""SELECT DATE(created_at) d, COUNT(*) v FROM datasets
                         WHERE qc_score IS NOT NULL AND DATE(created_at) >= DATE('now','localtime', ?) GROUP BY DATE(created_at)""")
    tasks_done = series("""SELECT DATE(finished_at) d, COUNT(*) v FROM tasks
                           WHERE status='done' AND DATE(finished_at) >= DATE('now','localtime', ?) GROUP BY DATE(finished_at)""")
    conn.close()
    from datetime import date, timedelta
    out = []
    for i in range(days - 1, -1, -1):
        d = (date.today() - timedelta(days=i)).isoformat()
        total = qc_total.get(d, 0)
        out.append({"date": d, "km": round(km.get(d, 0), 1), "data_bytes": data.get(d, 0),
                    "qc_pass_rate": round(qc_pass.get(d, 0) * 100.0 / total, 1) if total else None,
                    "tasks_done": tasks_done.get(d, 0)})
    return out


# ---------- Coverage gaps (覆盖缺口) ----------
@app.get("/api/coverage/gaps")
def coverage_gaps(min_points: int = 5):
    """0.005°网格聚合轨迹点，返回采集不足（<min_points）的网格中心，用于补采提示。"""
    conn = get_conn()
    rows = conn.execute(
        """SELECT CAST(lat/0.005 AS INT) glat, CAST(lng/0.005 AS INT) glng, COUNT(*) n
           FROM track_points GROUP BY glat, glng""").fetchall()
    conn.close()
    gaps = [{"lat": (r["glat"] + 0.5) * 0.005, "lng": (r["glng"] + 0.5) * 0.005, "points": r["n"]}
            for r in rows if r["n"] < min_points]
    return {"cell_deg": 0.005, "gaps": gaps}


# ---------- Frontend ----------
app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")


if __name__ == "__main__":
    import threading
    import webbrowser
    import uvicorn
    threading.Timer(1.5, lambda: webbrowser.open("http://127.0.0.1:8080")).start()
    uvicorn.run(app, host="127.0.0.1", port=8080)
