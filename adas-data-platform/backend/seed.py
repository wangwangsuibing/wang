"""Seed sample data (Shanghai area) on first run."""
import json
import math
import random

from database import get_conn

CENTER = (31.2304, 121.4737)  # Shanghai People's Square (WGS-84 approx)


def _rand_near(lat, lng, r=0.05):
    return round(lat + random.uniform(-r, r), 6), round(lng + random.uniform(-r, r), 6)


def _path_length_km(coords):
    total = 0.0
    for a, b in zip(coords, coords[1:]):
        dlat = (b[0] - a[0]) * 111.0
        dlng = (b[1] - a[1]) * 111.0 * math.cos(math.radians(a[0]))
        total += math.hypot(dlat, dlng)
    return round(total, 2)


def seed_if_empty():
    conn = get_conn()
    if conn.execute("SELECT COUNT(*) c FROM vehicles").fetchone()["c"] > 0:
        conn.close()
        return

    # vehicles
    vehicles = [
        ("采集车-01", "沪A·D1234"),
        ("采集车-02", "沪A·D5678"),
        ("采集车-03", "沪B·D9012"),
        ("采集车-04", "沪C·D3456"),
    ]
    for name, plate in vehicles:
        lat, lng = _rand_near(*CENTER, 0.03)
        conn.execute(
            "INSERT INTO vehicles (name, plate, lat, lng, battery) VALUES (?,?,?,?,?)",
            (name, plate, lat, lng, random.randint(55, 100)),
        )

    # points
    types = ["poi", "event", "obstacle"]
    weathers = ["晴", "多云", "小雨", ""]
    lights = ["白天", "夜间", "黄昏", ""]
    roads = ["城市道路", "高架", "隧道", "路口", ""]
    for i in range(40):
        lat, lng = _rand_near(*CENTER, 0.06)
        conn.execute(
            "INSERT INTO points (name, lat, lng, type, weather, lighting, road, note) VALUES (?,?,?,?,?,?,?,?)",
            (f"采集点-{i+1:02d}", lat, lng, random.choice(types),
             random.choice(weathers), random.choice(lights), random.choice(roads), ""),
        )

    # paths: a few polylines
    for i in range(3):
        start = _rand_near(*CENTER, 0.04)
        coords = [list(start)]
        for _ in range(8):
            last = coords[-1]
            coords.append([round(last[0] + random.uniform(-0.008, 0.012), 6),
                           round(last[1] + random.uniform(-0.008, 0.012), 6)])
        colors = ["#2d8cf0", "#19be6b", "#9b59b6"]
        conn.execute(
            "INSERT INTO paths (name, coords, color, length_km) VALUES (?,?,?,?)",
            (f"采集路线-{chr(65+i)}", json.dumps(coords), colors[i], _path_length_km(coords)),
        )

    # tasks
    conn.execute("INSERT INTO tasks (name, vehicle_id, path_id, priority, status, dispatched_at) VALUES ('浦西城区数据采集', 1, 1, 'high', 'running', datetime('now','localtime'))")
    conn.execute("INSERT INTO tasks (name, vehicle_id, path_id, priority, status, dispatched_at) VALUES ('高架匝道场景采集', 2, 2, 'normal', 'running', datetime('now','localtime'))")
    conn.execute("INSERT INTO tasks (name, path_id, priority, status) VALUES ('夜间隧道场景采集', 3, 'urgent', 'pending')")

    # historical track points for heatmap
    for vid in (1, 2, 3):
        base = _rand_near(*CENTER, 0.03)
        lat, lng = base
        for _ in range(150):
            lat += random.uniform(-0.002, 0.003)
            lng += random.uniform(-0.002, 0.003)
            conn.execute(
                "INSERT INTO track_points (vehicle_id, lat, lng, speed) VALUES (?,?,?,?)",
                (vid, round(lat, 6), round(lng, 6), round(random.uniform(10, 70), 1)),
            )

    # geofence
    d = 0.045
    fence = [[CENTER[0]-d, CENTER[1]-d], [CENTER[0]-d, CENTER[1]+d],
             [CENTER[0]+d, CENTER[1]+d], [CENTER[0]+d, CENTER[1]-d]]
    conn.execute("INSERT INTO geofences (name, coords) VALUES (?,?)", ("核心采集区", json.dumps(fence)))

    conn.execute("INSERT INTO alerts (vehicle_id, level, message) VALUES (3, 'info', '系统初始化完成，示例数据已加载')")
    conn.commit()
    conn.close()
