"""Vehicle movement simulator: moves collecting vehicles along their task paths,
records track points, and raises alerts."""
import json
import math
import random
import threading
import time

from database import get_conn


def _interp(coords, t):
    """t in [0,1) -> position along polyline."""
    n = len(coords)
    if n < 2:
        return coords[0]
    seg = t * (n - 1)
    i = min(int(seg), n - 2)
    f = seg - i
    lat = coords[i][0] + (coords[i + 1][0] - coords[i][0]) * f
    lng = coords[i][1] + (coords[i + 1][1] - coords[i][1]) * f
    return [lat, lng]


class Simulator:
    def __init__(self, interval=2.0):
        self.interval = interval
        self.progress = {}  # vehicle_id -> t
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as e:
                print("simulator error:", e)
            time.sleep(self.interval)

    def _tick(self):
        conn = get_conn()
        rows = conn.execute(
            """SELECT t.id AS task_id, t.vehicle_id, t.driver_id, p.coords
               FROM tasks t JOIN paths p ON t.path_id = p.id
               WHERE t.status = 'running' AND t.vehicle_id IS NOT NULL"""
        ).fetchall()
        for r in rows:
            vid = r["vehicle_id"]
            coords = json.loads(r["coords"])
            t = self.progress.get(vid, 0.0) + random.uniform(0.01, 0.03)
            if t >= 1.0:
                # task finished
                conn.execute(
                    "UPDATE tasks SET status='done', progress=100, finished_at=datetime('now','localtime') WHERE id=?",
                    (r["task_id"],),
                )
                conn.execute("UPDATE vehicles SET status='idle', speed=0 WHERE id=?", (vid,))
                if r["driver_id"]:
                    conn.execute("UPDATE drivers SET status='available' WHERE id=?", (r["driver_id"],))
                conn.execute(
                    "INSERT INTO alerts (vehicle_id, level, message) VALUES (?,?,?)",
                    (vid, "info", f"任务 #{r['task_id']} 采集完成"),
                )
                # simulate vehicle-side data package upload registration
                tname = conn.execute("SELECT name FROM tasks WHERE id=?", (r["task_id"],)).fetchone()["name"]
                event = random.choice(["", "", "manual", "AEB", "cutin"])  # 部分数据包带事件触发类型
                conn.execute(
                    """INSERT INTO datasets (name, task_id, vehicle_id, sensors, status, duration_s,
                       event_type, priority, upload_progress, note)
                       VALUES (?,?,?,?, 'uploading', ?, ?, ?, 0, '任务完成后由车端自动创建，回传中')""",
                    (f"{tname}-采集数据包", r["task_id"], vid,
                     json.dumps(["camera", "lidar", "gnss", "can"]),
                     round(random.uniform(300, 1800), 0), event,
                     "high" if event else "normal"),
                )
                self.progress.pop(vid, None)
                continue
            self.progress[vid] = t
            conn.execute("UPDATE tasks SET progress=? WHERE id=?", (round(t * 100, 1), r["task_id"]))
            lat, lng = _interp(coords, t)
            speed = round(random.uniform(20, 60), 1)
            conn.execute(
                """UPDATE vehicles SET lat=?, lng=?, speed=?, battery=MAX(0, battery-0.1),
                   status='collecting', updated_at=datetime('now','localtime') WHERE id=?""",
                (lat, lng, speed, vid),
            )
            conn.execute(
                "INSERT INTO track_points (vehicle_id, lat, lng, speed) VALUES (?,?,?,?)",
                (vid, lat, lng, speed),
            )
            # random low-probability alert
            if random.random() < 0.02:
                conn.execute(
                    "INSERT INTO alerts (vehicle_id, level, message) VALUES (?,?,?)",
                    (vid, "warning", "传感器数据抖动，已自动重试"),
                )
        # simulate in-flight vehicle→cloud upload progress (high priority first)
        ups = conn.execute(
            """SELECT id, upload_progress FROM datasets WHERE status='uploading'
               ORDER BY CASE priority WHEN 'high' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END, id LIMIT 2"""
        ).fetchall()
        for u in ups:
            p = min(100.0, (u["upload_progress"] or 0) + random.uniform(4, 12))
            if p >= 100.0:
                conn.execute(
                    """UPDATE datasets SET upload_progress=100, status='uploaded',
                       uploaded_at=datetime('now','localtime') WHERE id=?""", (u["id"],))
                conn.execute("INSERT INTO alerts (vehicle_id, level, message) VALUES (?,?,?)",
                             (None, "info", f"数据包 #{u['id']} 回传完成，等待质检"))
            else:
                conn.execute("UPDATE datasets SET upload_progress=? WHERE id=?", (round(p, 1), u["id"]))
        # battery alerts
        low = conn.execute("SELECT id, name, battery FROM vehicles WHERE battery < 20 AND status != 'offline'").fetchall()
        for v in low:
            exists = conn.execute(
                "SELECT 1 FROM alerts WHERE vehicle_id=? AND message LIKE '电量不足%' AND read=0", (v["id"],)
            ).fetchone()
            if not exists:
                conn.execute(
                    "INSERT INTO alerts (vehicle_id, level, message) VALUES (?,?,?)",
                    (v["id"], "critical", f"电量不足 ({v['battery']:.0f}%)，请及时返场充电"),
                )
        conn.commit()
        conn.close()


simulator = Simulator()
