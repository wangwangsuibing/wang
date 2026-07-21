"""Dataset (采集数据包) management: upload, checksum, QC, tags, search, export, lifecycle."""
import hashlib
import io
import json
import os
import random
import uuid
import zipfile

from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from database import get_conn, rows_to_dicts
from models import DatasetIn, TagsUpdate

router = APIRouter(prefix="/api/datasets", tags=["datasets"])

DATASET_DIR = None  # set by main.py

MAX_FILE = 500 * 1024 * 1024  # 500MB per file

# filename extension -> sensor data category
CATEGORY_MAP = {
    ".mp4": "camera", ".avi": "camera", ".h264": "camera", ".h265": "camera",
    ".jpg": "camera", ".jpeg": "camera", ".png": "camera",
    ".pcd": "lidar", ".bin": "lidar", ".las": "lidar", ".ply": "lidar",
    ".csv": "gnss", ".gpx": "gnss", ".nmea": "gnss",
    ".asc": "can", ".blf": "can", ".dbc": "can", ".mf4": "can",
    ".bag": "log", ".mcap": "log", ".log": "log", ".txt": "log", ".json": "log",
}


def _guess_category(name: str) -> str:
    return CATEGORY_MAP.get(os.path.splitext(name.lower())[1], "other")


def _dataset_or_404(conn, did: int):
    row = conn.execute("SELECT * FROM datasets WHERE id=?", (did,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "数据包不存在")
    return row


def _decorate(d: dict) -> dict:
    d["sensors"] = json.loads(d.get("sensors") or "[]")
    d["tags"] = json.loads(d.get("tags") or "[]")
    if d.get("qc_report"):
        d["qc_report"] = json.loads(d["qc_report"])
    return d


# ---------- CRUD & search ----------
@router.get("/meta/tags")
def all_tags():
    conn = get_conn()
    rows = conn.execute("SELECT tags FROM datasets").fetchall()
    conn.close()
    out = set()
    for r in rows:
        out.update(json.loads(r["tags"] or "[]"))
    return sorted(out)


@router.get("")
def list_datasets(status: str | None = None, tag: str | None = None,
                  keyword: str | None = None, task_id: int | None = None):
    conn = get_conn()
    q = """SELECT d.*, t.name AS task_name, v.name AS vehicle_name,
                  (SELECT COUNT(*) FROM dataset_files f WHERE f.dataset_id=d.id) AS file_count
           FROM datasets d LEFT JOIN tasks t ON d.task_id=t.id
           LEFT JOIN vehicles v ON d.vehicle_id=v.id WHERE 1=1"""
    args = []
    if status:
        q += " AND d.status=?"; args.append(status)
    if tag:
        q += " AND d.tags LIKE ?"; args.append(f'%"{tag}"%')
    if keyword:
        q += " AND (d.name LIKE ? OR d.note LIKE ?)"; args += [f"%{keyword}%"] * 2
    if task_id is not None:
        q += " AND d.task_id=?"; args.append(task_id)
    rows = conn.execute(q + " ORDER BY d.id DESC", args).fetchall()
    conn.close()
    return [_decorate(d) for d in rows_to_dicts(rows)]


@router.post("")
def create_dataset(d: DatasetIn):
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO datasets (name, task_id, vehicle_id, sensors, tags, duration_s, note) VALUES (?,?,?,?,?,?,?)",
        (d.name, d.task_id, d.vehicle_id, json.dumps(d.sensors, ensure_ascii=False),
         json.dumps(d.tags, ensure_ascii=False), d.duration_s, d.note))
    conn.commit(); conn.close()
    return {"id": cur.lastrowid}


@router.get("/{did}")
def get_dataset(did: int):
    conn = get_conn()
    row = _dataset_or_404(conn, did)
    files = conn.execute("SELECT * FROM dataset_files WHERE dataset_id=? ORDER BY id", (did,)).fetchall()
    conn.close()
    out = _decorate(dict(row))
    out["files"] = rows_to_dicts(files)
    return out


@router.delete("/{did}")
def delete_dataset(did: int):
    conn = get_conn()
    _dataset_or_404(conn, did)
    files = conn.execute("SELECT filename FROM dataset_files WHERE dataset_id=?", (did,)).fetchall()
    for f in files:
        try:
            os.remove(os.path.join(DATASET_DIR, f["filename"]))
        except OSError:
            pass
    conn.execute("DELETE FROM dataset_files WHERE dataset_id=?", (did,))
    conn.execute("DELETE FROM datasets WHERE id=?", (did,))
    conn.commit(); conn.close()
    return {"ok": True}


# ---------- file upload / download ----------
@router.post("/{did}/files")
async def upload_files(did: int, files: list[UploadFile] = File(...)):
    conn = get_conn()
    ds = _dataset_or_404(conn, did)
    if ds["status"] == "archived":
        conn.close(); raise HTTPException(400, "已归档数据包不可再上传")
    results = []
    total = 0
    for file in files:
        data = await file.read()
        if len(data) > MAX_FILE:
            conn.close(); raise HTTPException(400, f"{file.filename} 过大（单文件限 500MB）")
        sha = hashlib.sha256(data).hexdigest()
        # dedup within dataset by checksum
        dup = conn.execute("SELECT id FROM dataset_files WHERE dataset_id=? AND sha256=?", (did, sha)).fetchone()
        if dup:
            results.append({"name": file.filename, "skipped": True, "reason": "重复文件（校验和一致）"})
            continue
        ext = os.path.splitext(file.filename or "")[1]
        stored = f"{uuid.uuid4().hex}{ext}"
        with open(os.path.join(DATASET_DIR, stored), "wb") as f:
            f.write(data)
        cat = _guess_category(file.filename or "")
        conn.execute(
            "INSERT INTO dataset_files (dataset_id, filename, orig_name, category, size, sha256) VALUES (?,?,?,?,?,?)",
            (did, stored, file.filename or stored, cat, len(data), sha))
        total += len(data)
        results.append({"name": file.filename, "size": len(data), "sha256": sha, "category": cat})
    conn.execute(
        """UPDATE datasets SET size_bytes = size_bytes + ?,
           status = CASE WHEN status='uploading' THEN 'uploaded' ELSE status END,
           uploaded_at = datetime('now','localtime') WHERE id=?""", (total, did))
    conn.commit(); conn.close()
    return {"files": results, "added_bytes": total}


@router.get("/files/{fid}/download")
def download_file(fid: int):
    conn = get_conn()
    row = conn.execute("SELECT * FROM dataset_files WHERE id=?", (fid,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "文件不存在")
    path = os.path.join(DATASET_DIR, row["filename"])
    if not os.path.exists(path):
        raise HTTPException(404, "文件已丢失")
    return FileResponse(path, filename=row["orig_name"])


@router.delete("/files/{fid}")
def delete_file(fid: int):
    conn = get_conn()
    row = conn.execute("SELECT * FROM dataset_files WHERE id=?", (fid,)).fetchone()
    if not row:
        conn.close(); raise HTTPException(404, "文件不存在")
    conn.execute("DELETE FROM dataset_files WHERE id=?", (fid,))
    conn.execute("UPDATE datasets SET size_bytes = MAX(0, size_bytes - ?) WHERE id=?",
                 (row["size"], row["dataset_id"]))
    conn.commit(); conn.close()
    try:
        os.remove(os.path.join(DATASET_DIR, row["filename"]))
    except OSError:
        pass
    return {"ok": True}


# ---------- quality check (simulated pipeline) ----------
@router.post("/{did}/qc")
def run_qc(did: int):
    conn = get_conn()
    _dataset_or_404(conn, did)
    files = rows_to_dicts(conn.execute("SELECT * FROM dataset_files WHERE dataset_id=?", (did,)).fetchall())
    if not files:
        conn.close(); raise HTTPException(400, "数据包内没有文件，无法质检")
    checks = []
    score = 100.0

    # 1. checksum integrity
    bad = []
    for f in files:
        path = os.path.join(DATASET_DIR, f["filename"])
        ok = False
        if os.path.exists(path):
            h = hashlib.sha256()
            with open(path, "rb") as fp:
                for chunk in iter(lambda: fp.read(1 << 20), b""):
                    h.update(chunk)
            ok = h.hexdigest() == f["sha256"]
        if not ok:
            bad.append(f["orig_name"])
    if bad:
        score -= 40
    checks.append({"item": "文件完整性校验 (SHA-256)", "passed": not bad,
                   "detail": "全部通过" if not bad else f"校验失败: {', '.join(bad)}"})

    # 2. sensor coverage
    cats = {f["category"] for f in files}
    core = {"camera", "lidar", "gnss", "can"}
    missing = core - cats
    if missing:
        score -= 5 * len(missing)
    names = {"camera": "相机", "lidar": "激光雷达", "gnss": "定位", "can": "总线"}
    checks.append({"item": "传感器数据完备性", "passed": not missing,
                   "detail": "核心传感器数据齐全" if not missing else "缺少: " + ", ".join(names[m] for m in missing)})

    # 3. empty file check
    empty = [f["orig_name"] for f in files if f["size"] == 0]
    if empty:
        score -= 20
    checks.append({"item": "空文件检测", "passed": not empty,
                   "detail": "无空文件" if not empty else f"空文件: {', '.join(empty)}"})

    # 4. simulated frame-drop / time-sync analysis
    drop_rate = round(random.uniform(0.0, 2.0), 2)
    sync_err = round(random.uniform(0.1, 8.0), 1)
    if drop_rate > 1.0:
        score -= 10
    if sync_err > 5.0:
        score -= 10
    checks.append({"item": "丢帧率分析", "passed": drop_rate <= 1.0, "detail": f"丢帧率 {drop_rate}%（阈值 1%）"})
    checks.append({"item": "多传感器时间同步", "passed": sync_err <= 5.0, "detail": f"最大时间偏差 {sync_err}ms（阈值 5ms）"})

    score = max(0.0, round(score, 1))
    status = "qc_passed" if score >= 60 else "qc_failed"
    report = {"score": score, "checks": checks}
    conn.execute("UPDATE datasets SET status=?, qc_score=?, qc_report=? WHERE id=?",
                 (status, score, json.dumps(report, ensure_ascii=False), did))
    conn.execute("INSERT INTO alerts (vehicle_id, level, message) VALUES (?,?,?)",
                 (None, "info" if status == "qc_passed" else "warning",
                  f"数据包 #{did} 质检{'通过' if status == 'qc_passed' else '未通过'}（{score} 分）"))
    conn.commit(); conn.close()
    return report


# ---------- tags ----------
@router.put("/{did}/tags")
def update_tags(did: int, t: TagsUpdate):
    conn = get_conn()
    _dataset_or_404(conn, did)
    conn.execute("UPDATE datasets SET tags=? WHERE id=?",
                 (json.dumps([s.strip() for s in t.tags if s.strip()], ensure_ascii=False), did))
    conn.commit(); conn.close()
    return {"ok": True}


# ---------- lifecycle ----------
@router.post("/{did}/archive")
def archive_dataset(did: int):
    conn = get_conn()
    _dataset_or_404(conn, did)
    conn.execute("UPDATE datasets SET status='archived', archived_at=datetime('now','localtime') WHERE id=?", (did,))
    conn.commit(); conn.close()
    return {"ok": True}


@router.post("/{did}/restore")
def restore_dataset(did: int):
    conn = get_conn()
    ds = _dataset_or_404(conn, did)
    if ds["status"] != "archived":
        conn.close(); raise HTTPException(400, "仅归档数据包可恢复")
    new_status = "qc_passed" if ds["qc_score"] and ds["qc_score"] >= 60 else "uploaded"
    conn.execute("UPDATE datasets SET status=?, archived_at=NULL WHERE id=?", (new_status, did))
    conn.commit(); conn.close()
    return {"ok": True}


# ---------- export ----------
@router.get("/{did}/manifest")
def export_manifest(did: int):
    """Export dataset manifest (metadata + file list with checksums) as JSON."""
    out = get_dataset(did)
    return JSONResponse(out, headers={
        "Content-Disposition": f"attachment; filename=dataset_{did}_manifest.json"})


@router.get("/{did}/download.zip")
def download_zip(did: int):
    """Package all files + manifest into a zip for delivery to downstream platforms."""
    detail = get_dataset(did)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(detail, ensure_ascii=False, indent=2))
        for f in detail["files"]:
            path = os.path.join(DATASET_DIR, f["filename"])
            if os.path.exists(path):
                z.write(path, arcname=f"{f['category']}/{f['orig_name']}")
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/zip", headers={
        "Content-Disposition": f"attachment; filename=dataset_{did}.zip"})
