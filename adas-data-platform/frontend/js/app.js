// Main application logic
(() => {
  const { map, layers, setHeat, ll } = MapMod;

  // ---------- state ----------
  let mode = 'browse';           // browse | addPoint | drawPath | drawFence
  let drawCoords = [];           // WGS-84 coords being drawn
  let drawLine = null;
  let heatOn = false;
  let gapsOn = false;
  let replayTimer = null;
  let cachedPaths = [], cachedVehicles = [];

  const $ = id => document.getElementById(id);

  // ---------- icons ----------
  const pointColors = { poi: '#2d8cf0', event: '#ff5722', obstacle: '#e91e63', start: '#19be6b', end: '#616161' };
  const typeNames = { poi: '兴趣点', event: '事件点', obstacle: '障碍物', start: '起点', end: '终点' };
  function dotIcon(color) {
    return L.divIcon({
      className: '', iconSize: [14, 14], iconAnchor: [7, 7],
      html: `<div style="width:14px;height:14px;border-radius:50%;background:${color};border:2px solid #fff;box-shadow:0 0 4px rgba(0,0,0,.5)"></div>`,
    });
  }
  function carIcon(status) {
    const c = status === 'collecting' ? '#19be6b' : status === 'offline' ? '#999' : '#ff9900';
    return L.divIcon({
      className: '', iconSize: [30, 30], iconAnchor: [15, 15],
      html: `<div style="font-size:22px;filter:drop-shadow(0 0 3px ${c})">🚗</div>`,
    });
  }

  // ---------- mode handling ----------
  function setMode(m) {
    mode = mode === m ? 'browse' : m;
    drawCoords = [];
    if (drawLine) { layers.draw.removeLayer(drawLine); drawLine = null; }
    layers.draw.clearLayers();
    document.querySelectorAll('.mode-btn').forEach(b => b.classList.remove('active'));
    if (mode !== 'browse') $(`btn-${mode}`).classList.add('active');
    $('draw-actions').style.display = (mode === 'drawPath' || mode === 'drawFence') ? 'flex' : 'none';
    map.getContainer().style.cursor = mode === 'browse' ? '' : 'crosshair';
    const tips = { addPoint: '点击地图添加采集点', drawPath: '连续点击绘制路径，完成后点“保存”', drawFence: '连续点击绘制围栏（≥3点），完成后点“保存”' };
    $('mode-tip').textContent = tips[mode] || '';
  }

  map.on('click', async e => {
    const [lat, lng] = Coords.gcj2wgs(e.latlng.lat, e.latlng.lng);
    if (mode === 'addPoint') {
      openPointForm(lat, lng);
    } else if (mode === 'drawPath' || mode === 'drawFence') {
      drawCoords.push([lat, lng]);
      const gcj = drawCoords.map(c => ll(c[0], c[1]));
      if (drawLine) layers.draw.removeLayer(drawLine);
      drawLine = mode === 'drawPath'
        ? L.polyline(gcj, { color: '#ff5722', dashArray: '6 4' })
        : L.polygon(gcj, { color: '#ff9900', dashArray: '6 4' });
      drawLine.addTo(layers.draw);
    }
  });

  $('btn-save-draw').onclick = async () => {
    try {
      if (mode === 'drawPath') {
        if (drawCoords.length < 2) return toast('路径至少 2 个点', true);
        const name = prompt('路径名称：', '采集路线-' + new Date().toLocaleTimeString());
        if (!name) return;
        await API.post('/api/paths', { name, coords: drawCoords });
        toast('路径已保存');
      } else if (mode === 'drawFence') {
        if (drawCoords.length < 3) return toast('围栏至少 3 个点', true);
        const name = prompt('围栏名称：', '采集区-' + new Date().toLocaleTimeString());
        if (!name) return;
        await API.post('/api/geofences', { name, coords: drawCoords });
        toast('围栏已保存');
      }
      setMode('browse');
      refreshAll();
    } catch (err) { toast(err.message, true); }
  };
  $('btn-cancel-draw').onclick = () => setMode('browse');
  $('btn-addPoint').onclick = () => setMode('addPoint');
  $('btn-drawPath').onclick = () => setMode('drawPath');
  $('btn-drawFence').onclick = () => setMode('drawFence');

  // ---------- point form ----------
  function openPointForm(lat, lng) {
    $('pf-lat').value = lat.toFixed(6);
    $('pf-lng').value = lng.toFixed(6);
    $('pf-name').value = '';
    $('pf-note').value = '';
    $('point-form').style.display = 'block';
  }
  $('pf-cancel').onclick = () => ($('point-form').style.display = 'none');
  $('pf-save').onclick = async () => {
    try {
      await API.post('/api/points', {
        name: $('pf-name').value || '未命名点',
        lat: +$('pf-lat').value, lng: +$('pf-lng').value,
        type: $('pf-type').value, note: $('pf-note').value,
        task_id: $('pf-task').value ? +$('pf-task').value : null,
        weather: $('pf-weather').value, lighting: $('pf-lighting').value, road: $('pf-road').value,
      });
      $('point-form').style.display = 'none';
      toast('打点成功');
      loadPoints();
    } catch (err) { toast(err.message, true); }
  };

  // ---------- loaders ----------
  async function loadPoints() {
    const filter = $('point-filter').value;
    const pts = await API.get('/api/points' + (filter ? `?task_id=${filter}` : ''));
    layers.points.clearLayers();
    pts.forEach(p => {
      const m = L.marker(ll(p.lat, p.lng), { icon: dotIcon(pointColors[p.type] || '#2d8cf0') });
      const meta = [p.weather, p.lighting, p.road].filter(Boolean).join(' / ');
      const taskLine = p.task_id ? `任务: #${p.task_id} ${p.task_name || ''}<br>` : '';
      m.bindPopup(`<b>${p.name}</b><br>类型: ${typeNames[p.type] || p.type}<br>${taskLine}${meta ? '场景: ' + meta + '<br>' : ''}${p.note ? '备注: ' + p.note + '<br>' : ''}<small>${p.created_at}</small><br><a href="#" onclick="App.attachments(${p.id},'${(p.name||'').replace(/'/g,'')}');return false;">📎 附件</a> · <a href="#" onclick="App.delPoint(${p.id});return false;">🗑 删除</a>`);
      m.addTo(layers.points);
    });
    $('stat-points').textContent = pts.length;
  }
  $('point-filter').onchange = loadPoints;

  async function loadPaths() {
    cachedPaths = await API.get('/api/paths');
    layers.paths.clearLayers();
    const sels = [['task-path', '选择路线'], ['camp-path', '选择路线']].map(([id, ph]) => {
      const s = $(id); const v = s.value; s.innerHTML = `<option value="">${ph}</option>`; return [s, v];
    });
    cachedPaths.forEach(p => {
      const line = L.polyline(p.coords.map(c => ll(c[0], c[1])), { color: p.color, weight: 4, opacity: 0.85 });
      line.bindPopup(`<b>${p.name}</b><br>长度: ${p.length_km} km<br><a href="#" onclick="App.delPath(${p.id});return false;">🗑 删除</a>`);
      line.addTo(layers.paths);
      sels.forEach(([s]) => s.insertAdjacentHTML('beforeend', `<option value="${p.id}">${p.name} (${p.length_km}km)</option>`));
    });
    sels.forEach(([s, v]) => (s.value = v));
  }

  async function loadVehicles() {
    cachedVehicles = await API.get('/api/vehicles');
    layers.vehicles.clearLayers();
    const sel = $('task-vehicle'); sel.innerHTML = '<option value="">自动分配</option>';
    const list = $('vehicle-list'); list.innerHTML = '';
    const stNames = { idle: '空闲', collecting: '采集中', offline: '离线' };
    cachedVehicles.forEach(v => {
      const m = L.marker(ll(v.lat, v.lng), { icon: carIcon(v.status) });
      m.bindPopup(`<b>${v.name}</b> ${v.plate || ''}<br>状态: ${stNames[v.status] || v.status}<br>速度: ${v.speed} km/h · 电量: ${Math.round(v.battery)}%<br><a href="#" onclick="App.replay(${v.id});return false;">▶ 轨迹回放</a>`);
      m.addTo(layers.vehicles);
      sel.insertAdjacentHTML('beforeend', `<option value="${v.id}">${v.name}</option>`);
      list.insertAdjacentHTML('beforeend',
        `<div class="v-item"><span class="v-dot ${v.status}"></span><b>${v.name}</b><span class="v-meta">${stNames[v.status] || v.status} · ${v.speed}km/h · 🔋${Math.round(v.battery)}%</span></div>`);
    });
    renderCampVehicles();
  }

  async function loadGeofences() {
    const gs = await API.get('/api/geofences');
    layers.geofences.clearLayers();
    gs.forEach(g => {
      const poly = L.polygon(g.coords.map(c => ll(c[0], c[1])), { color: g.color, fillOpacity: 0.06, weight: 2, dashArray: '8 5' });
      poly.bindPopup(`<b>${g.name}</b><br><a href="#" onclick="App.delFence(${g.id});return false;">🗑 删除</a>`);
      poly.addTo(layers.geofences);
    });
  }

  async function loadTasks() {
    const tasks = await API.get('/api/tasks');
    // populate task selectors (point form + filter), preserving selection
    const pfSel = $('pf-task'), fSel = $('point-filter');
    const pfV = pfSel.value, fV = fSel.value;
    pfSel.innerHTML = '<option value="">不关联任务</option>';
    fSel.innerHTML = '<option value="">全部点位</option>';
    tasks.forEach(t => {
      if (t.status !== 'cancelled') pfSel.insertAdjacentHTML('beforeend', `<option value="${t.id}">#${t.id} ${t.name}</option>`);
      fSel.insertAdjacentHTML('beforeend', `<option value="${t.id}">#${t.id} ${t.name}</option>`);
    });
    pfSel.value = pfV; fSel.value = fV;
    const box = $('task-list'); box.innerHTML = '';
    const stNames = { pending: '待下发', running: '执行中', done: '已完成', cancelled: '已取消' };
    const priNames = { low: '低', normal: '中', high: '高', urgent: '紧急' };
    tasks.forEach(t => {
      const checklistBtn = t.status === 'pending' && !t.checklist_done
        ? `<button class="ghost" onclick="App.checklist(${t.id})">📋 出车检查</button>` : '';
      const actions = t.status === 'pending'
        ? `${checklistBtn}<button onclick="App.dispatch(${t.id})">下发</button><button class="ghost" onclick="App.cancelTask(${t.id})">取消</button>`
        : t.status === 'running'
        ? `<button class="ghost" onclick="App.cancelTask(${t.id})">终止</button>` : '';
      const prog = t.status === 'running'
        ? `<div style="background:#eee;border-radius:3px;height:6px;margin:4px 0"><div style="height:6px;border-radius:3px;width:${t.progress || 0}%;background:#19be6b"></div></div><small>${(t.progress || 0).toFixed(0)}%</small>`
        : t.status === 'done' ? '<small style="color:#19be6b">100%</small>' : '';
      const extra = [
        t.driver_name ? `👤${t.driver_name}` : '',
        t.sensor_config_name ? `🎛${t.sensor_config_name}` : '',
        t.campaign_name ? `📦${t.campaign_name}` : '',
        (t.event_rules && t.event_rules.length) ? `⚡${t.event_rules.map(r => r.trigger).join('/')}` : '',
        t.checklist_done ? '📋✓' : '',
      ].filter(Boolean).join(' · ');
      box.insertAdjacentHTML('beforeend', `
        <div class="task-item st-${t.status}">
          <div class="t-head"><b>#${t.id} ${t.name}</b><span class="badge b-${t.status}">${stNames[t.status] || t.status}</span></div>
          <div class="t-meta">优先级: ${priNames[t.priority] || t.priority} · 车辆: ${t.vehicle_name || '未分配'} · 路线: ${t.path_name || '-'}</div>
          ${extra ? `<div class="t-meta">${extra}</div>` : ''}
          ${prog}
          <div class="t-actions">${actions}</div>
        </div>`);
    });
  }

  // ---------- drivers / sensor configs / campaigns ----------
  async function loadDrivers() {
    const drivers = await API.get('/api/drivers');
    const stNames = { available: '空闲', on_task: '任务中', off: '休息' };
    const sel = $('task-driver'); const v = sel.value;
    sel.innerHTML = '<option value="">选择采集员</option>' +
      drivers.map(d => `<option value="${d.id}">${d.name}（${stNames[d.status] || d.status}）</option>`).join('');
    sel.value = v;
    const box = $('driver-list'); box.innerHTML = '';
    drivers.forEach(d => {
      box.insertAdjacentHTML('beforeend',
        `<div class="v-item"><span class="v-dot ${d.status === 'on_task' ? 'collecting' : 'idle'}"></span><b>${d.name}</b>
         <span class="v-meta">${stNames[d.status] || d.status} · ${d.phone || '-'} · 完成 ${d.tasks_done} 单 · <a href="#" onclick="App.delDriver(${d.id});return false;">🗑</a></span></div>`);
    });
  }
  $('btn-add-driver').onclick = async () => {
    const name = $('drv-name').value.trim();
    if (!name) return toast('请输入姓名', true);
    await API.post('/api/drivers', { name, phone: $('drv-phone').value.trim() });
    $('drv-name').value = ''; $('drv-phone').value = '';
    toast('采集员已添加'); loadDrivers();
  };

  async function loadSensorConfigs() {
    const scs = await API.get('/api/sensor_configs');
    for (const id of ['task-sensor', 'camp-sensor']) {
      const sel = $(id); const v = sel.value;
      sel.innerHTML = '<option value="">传感器配置</option>' +
        scs.map(s => `<option value="${s.id}" title="${JSON.stringify(s.config).replace(/"/g, '')}">${s.name}</option>`).join('');
      sel.value = v;
    }
  }

  function parseEventRules(str) {
    // "AEB:10:30, cutin:5:20" -> [{trigger, pre_s, post_s}]
    return str.split(/[,，]/).map(s => s.trim()).filter(Boolean).map(s => {
      const [trigger, pre, post] = s.split(':');
      return { trigger, pre_s: +pre || 10, post_s: +post || 30 };
    });
  }

  function renderCampVehicles() {
    $('camp-vehicles').innerHTML = '选择车辆: ' + cachedVehicles.map(v =>
      `<label style="margin-right:8px"><input type="checkbox" class="camp-v" value="${v.id}">${v.name}</label>`).join('');
  }
  $('btn-create-camp').onclick = async () => {
    const name = $('camp-name').value.trim();
    if (!name) return toast('请输入活动名称', true);
    const vids = [...document.querySelectorAll('.camp-v:checked')].map(x => +x.value);
    if (!vids.length) return toast('请至少选择一辆车', true);
    try {
      const r = await API.post('/api/campaigns', {
        name, vehicle_ids: vids,
        path_id: $('camp-path').value ? +$('camp-path').value : null,
        sensor_config_id: $('camp-sensor').value ? +$('camp-sensor').value : null,
      });
      $('camp-name').value = '';
      toast(`活动已创建，批量生成 ${r.task_ids.length} 个任务`);
      loadTasks();
    } catch (err) { toast(err.message, true); }
  };

  // ---------- storage ----------
  async function loadStorage() {
    const s = await API.get('/api/storage');
    const color = s.warning ? '#ed4014' : '#19be6b';
    $('storage-box').innerHTML =
      `<div style="background:#eee;border-radius:4px;height:10px;margin:4px 0"><div style="height:10px;border-radius:4px;width:${s.used_percent}%;background:${color}"></div></div>
       磁盘已用 ${s.used_percent}%（告警线 ${s.warn_percent}%）· 平台数据 ${fmtSize(s.upload_bytes)} · 剩余 ${fmtSize(s.disk_free)}` +
      (s.warning ? '<br><b style="color:#ed4014">⚠ 存储水位超过告警线，请清理或扩容</b>' : '');
  }

  // ---------- reports ----------
  function barChart(title, rows, valueFn, fmtFn) {
    const max = Math.max(...rows.map(valueFn), 1e-9);
    return `<div class="t-meta" style="margin-top:6px"><b>${title}</b></div>` +
      '<div style="display:flex;align-items:flex-end;gap:2px;height:56px">' +
      rows.map(r => {
        const v = valueFn(r);
        const h = v === null ? 0 : Math.max(2, v * 52 / max);
        return `<div title="${r.date}: ${fmtFn(v)}" style="flex:1;height:${h}px;background:#2d8cf0;border-radius:2px 2px 0 0;opacity:${v === null ? 0.15 : 0.9}"></div>`;
      }).join('') + '</div>';
  }
  async function loadReports() {
    const rows = await API.get('/api/reports/daily');
    $('report-charts').innerHTML =
      barChart('采集里程 (km)', rows, r => r.km, v => v + ' km') +
      barChart('数据量', rows, r => r.data_bytes, v => fmtSize(v)) +
      barChart('质检合格率 (%)', rows, r => r.qc_pass_rate, v => v === null ? '无质检' : v + '%') +
      barChart('完成任务数', rows, r => r.tasks_done, v => v + ' 个');
    const rules = await API.get('/api/datasets/meta/qc_rules');
    $('qc-drop').value = rules.drop_rate_max;
    $('qc-sync').value = rules.sync_err_max_ms;
    $('qc-pass').value = rules.pass_score;
    const ret = await API.get('/api/datasets/meta/retention');
    $('ret-days').value = ret.retention_days;
    loadAudit();
  }
  $('qc-save-rules').onclick = async () => {
    await API.put('/api/datasets/meta/qc_rules', {
      drop_rate_max: +$('qc-drop').value, sync_err_max_ms: +$('qc-sync').value,
      pass_score: +$('qc-pass').value,
      camera_exposure_check: true, lidar_density_check: true, gps_loss_check: true,
    });
    toast('质检规则已保存');
  };
  $('ret-save').onclick = async () => {
    await API.put('/api/datasets/meta/retention', { retention_days: +$('ret-days').value });
    toast('保留策略已保存');
  };
  $('ret-apply').onclick = async () => {
    const r = await API.post('/api/datasets/meta/retention/apply');
    toast(r.archived.length ? `已自动归档 ${r.archived.length} 个数据包` : '没有符合条件的数据包');
  };
  async function loadAudit() {
    const logs = await API.get('/api/audit?limit=50');
    $('audit-list').innerHTML = logs.map(l =>
      `<div class="v-item"><b>${l.action}</b> <span class="v-meta">${l.target} ${l.detail} · ${l.created_at}</span></div>`).join('') || '<p class="hint">暂无记录</p>';
  }

  // ---------- coverage gaps ----------
  $('btn-gaps').onclick = async () => {
    gapsOn = !gapsOn;
    $('btn-gaps').classList.toggle('active', gapsOn);
    layers.gaps.clearLayers();
    if (!gapsOn) return;
    const g = await API.get('/api/coverage/gaps');
    const d = g.cell_deg / 2;
    g.gaps.forEach(c => {
      L.rectangle([ll(c.lat - d, c.lng - d), ll(c.lat + d, c.lng + d)],
        { color: '#ed4014', weight: 1, fillOpacity: 0.25, dashArray: '4 3' })
        .bindPopup(`覆盖不足网格<br>仅 ${c.points} 个轨迹点，建议补采`)
        .addTo(layers.gaps);
    });
    toast(`发现 ${g.gaps.length} 个覆盖不足网格`);
  };

  async function loadStats() {
    const s = await API.get('/api/stats');
    $('stat-vehicles').textContent = `${s.vehicles_collecting}/${s.vehicles_total}`;
    $('stat-points').textContent = s.points_total;
    $('stat-paths').textContent = `${s.paths_total} 条 / ${s.paths_km.toFixed(1)} km`;
    $('stat-tasks').textContent = `${s.tasks_running} 执行中 / ${s.task_done_rate}% 完成率`;
    $('stat-tracks').textContent = s.track_points;
    $('stat-coverage').textContent = `${s.coverage_cells} 网格`;
    $('stat-datasets').textContent = `${s.datasets_qc_passed}/${s.datasets_total}`;
    $('stat-databytes').textContent = fmtSize(s.datasets_bytes);
    $('alert-badge').textContent = s.alerts_unread;
    $('alert-badge').style.display = s.alerts_unread > 0 ? 'inline-block' : 'none';
  }

  async function loadAlerts() {
    const alerts = await API.get('/api/alerts');
    const box = $('alert-list'); box.innerHTML = '';
    alerts.forEach(a => {
      box.insertAdjacentHTML('beforeend',
        `<div class="alert-item lv-${a.level}${a.read ? ' read' : ''}"><b>${a.vehicle_name || '系统'}</b> ${a.message}<br><small>${a.created_at}</small></div>`);
    });
  }

  // ---------- heatmap ----------
  $('btn-heatmap').onclick = async () => {
    heatOn = !heatOn;
    $('btn-heatmap').classList.toggle('active', heatOn);
    setHeat(heatOn ? await API.get('/api/heatmap') : null);
  };

  // ---------- attachments ----------
  let attPointId = null;
  async function loadAttachments() {
    const list = await API.get(`/api/points/${attPointId}/attachments`);
    const box = $('att-list'); box.innerHTML = list.length ? '' : '<p class="hint">暂无附件</p>';
    list.forEach(a => {
      const kb = (a.size / 1024).toFixed(1);
      box.insertAdjacentHTML('beforeend',
        `<div class="v-item">📄 <a href="/api/attachments/${a.id}/download">${a.orig_name}</a><span class="v-meta">${kb} KB · <a href="#" onclick="App.delAtt(${a.id});return false;">🗑</a></span></div>`);
    });
  }
  $('att-close').onclick = () => ($('att-modal').style.display = 'none');
  $('att-upload').onclick = async () => {
    const f = $('att-file').files[0];
    if (!f) return toast('请先选择文件', true);
    const fd = new FormData();
    fd.append('file', f);
    $('att-upload').disabled = true;
    try {
      const r = await fetch(`/api/points/${attPointId}/attachments`, { method: 'POST', body: fd });
      if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
      toast('上传成功');
      $('att-file').value = '';
      loadAttachments();
    } catch (err) { toast(err.message, true); }
    finally { $('att-upload').disabled = false; }
  };

  // ---------- datasets ----------
  function fmtSize(b) {
    if (!b) return '0 B';
    const u = ['B', 'KB', 'MB', 'GB', 'TB'];
    let i = 0;
    while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; }
    return b.toFixed(i ? 1 : 0) + ' ' + u[i];
  }
  const dsStatusNames = { uploading: '回传中', uploaded: '已上传', qc_running: '质检中', qc_passed: '质检通过', qc_failed: '质检未通过', archived: '已归档' };
  const dsStatusColors = { uploading: '#ff9900', uploaded: '#2d8cf0', qc_passed: '#19be6b', qc_failed: '#ed4014', archived: '#999' };
  const anonNames = { pending: '待脱敏', running: '脱敏中', done: '已脱敏', not_required: '无需脱敏' };
  let dsCurrentId = null;

  async function loadDatasets() {
    const params = new URLSearchParams();
    if ($('ds-status-filter').value) params.set('status', $('ds-status-filter').value);
    if ($('ds-tag-filter').value) params.set('tag', $('ds-tag-filter').value);
    if ($('ds-keyword').value.trim()) params.set('keyword', $('ds-keyword').value.trim());
    let list = await API.get('/api/datasets' + (params.toString() ? '?' + params : ''));
    if ($('ds-event-filter').value) list = list.filter(d => d.event_type === $('ds-event-filter').value);
    const box = $('ds-list');
    box.innerHTML = list.length ? '' : '<p class="hint">暂无数据包</p>';
    list.forEach(d => {
      const tags = d.tags.map(t => `<span class="badge" style="background:#eef4ff;color:#2d8cf0">${t}</span>`).join(' ');
      const qc = d.qc_score != null ? ` · 质检 ${d.qc_score} 分` : '';
      const ev = d.event_type ? ` · ⚡${d.event_type}` : '';
      const anon = ` · 🔒${anonNames[d.anonymized] || d.anonymized}`;
      const upl = d.status === 'uploading'
        ? `<div style="background:#eee;border-radius:3px;height:6px;margin:4px 0"><div style="height:6px;border-radius:3px;width:${d.upload_progress || 0}%;background:#ff9900"></div></div><small>回传 ${(d.upload_progress || 0).toFixed(0)}%</small>`
        : '';
      const recollect = d.status === 'qc_failed'
        ? `<button onclick="App.recollect(${d.id})">🔁 一键补采</button>` : '';
      box.insertAdjacentHTML('beforeend', `
        <div class="task-item">
          <div class="t-head"><b>#${d.id} ${d.name}</b>
            <span class="badge" style="background:${dsStatusColors[d.status] || '#999'};color:#fff">${dsStatusNames[d.status] || d.status}</span></div>
          <div class="t-meta">${d.vehicle_name || '手动创建'} · ${d.file_count} 个文件 · ${fmtSize(d.size_bytes)}${qc}${ev}${anon}</div>
          <div class="t-meta">${tags}</div>
          ${upl}
          <div class="t-actions">
            <button onclick="App.openDataset(${d.id})">详情/上传</button>
            ${recollect}
            <button class="ghost" onclick="App.delDataset(${d.id})">🗑 删除</button>
          </div>
        </div>`);
    });
    // tag filter options
    const tags = await API.get('/api/datasets/meta/tags');
    const sel = $('ds-tag-filter'); const cur = sel.value;
    sel.innerHTML = '<option value="">全部标签</option>' + tags.map(t => `<option>${t}</option>`).join('');
    sel.value = cur;
    // task selector for new datasets
    const tasks = await API.get('/api/tasks');
    const tSel = $('ds-task'); const tv = tSel.value;
    tSel.innerHTML = '<option value="">不关联任务</option>' +
      tasks.map(t => `<option value="${t.id}">#${t.id} ${t.name}</option>`).join('');
    tSel.value = tv;
  }
  $('ds-status-filter').onchange = loadDatasets;
  $('ds-tag-filter').onchange = loadDatasets;
  $('ds-event-filter').onchange = loadDatasets;
  let dsKwTimer; $('ds-keyword').oninput = () => { clearTimeout(dsKwTimer); dsKwTimer = setTimeout(loadDatasets, 400); };

  $('btn-create-ds').onclick = async () => {
    const name = $('ds-name').value.trim();
    if (!name) return toast('请输入数据包名称', true);
    const tags = $('ds-tags').value.split(/[,，]/).map(s => s.trim()).filter(Boolean);
    try {
      const r = await API.post('/api/datasets', { name, tags, task_id: $('ds-task').value ? +$('ds-task').value : null });
      $('ds-name').value = ''; $('ds-tags').value = '';
      toast('数据包已创建，请上传文件');
      await loadDatasets();
      App.openDataset(r.id);
    } catch (err) { toast(err.message, true); }
  };

  async function loadDatasetDetail() {
    const d = await API.get('/api/datasets/' + dsCurrentId);
    $('ds-title').textContent = `💾 #${d.id} ${d.name}`;
    $('ds-meta').innerHTML =
      `状态: <b style="color:${dsStatusColors[d.status] || '#999'}">${dsStatusNames[d.status] || d.status}</b>` +
      ` · ${fmtSize(d.size_bytes)} · 时长 ${d.duration_s || 0}s · ${d.created_at}` +
      (d.task_name ? `<br>任务: #${d.task_id} ${d.task_name} · 车辆: ${d.vehicle_name || '-'}` : '');
    $('ds-edit-tags').value = d.tags.join(',');
    $('ds-priority').value = d.priority || 'normal';
    $('ds-anon-state').textContent = anonNames[d.anonymized] || d.anonymized;
    // lineage
    const cons = await API.get(`/api/datasets/${dsCurrentId}/consumers`);
    $('ds-lineage').innerHTML = cons.length
      ? cons.map(c => `<div class="v-item">→ <b>${c.consumer}</b><span class="v-meta">${c.note || ''} · ${c.created_at}</span></div>`).join('')
      : '<p class="hint">尚未被下游消费</p>';
    // QC report
    const qcBox = $('ds-qc-report');
    if (d.qc_report) {
      qcBox.innerHTML = `<b>质检报告 · ${d.qc_report.score} 分</b>` + d.qc_report.checks.map(c =>
        `<div class="t-meta">${c.passed ? '✅' : '❌'} ${c.item} — ${c.detail}</div>`).join('');
    } else qcBox.innerHTML = '<span class="hint">尚未质检</span>';
    // files
    const catNames = { camera: '📷 相机', lidar: '🔦 激光雷达', radar: '📡 毫米波', gnss: '🛰 定位', can: '🚌 总线', log: '📜 日志', other: '📄 其他' };
    const fb = $('ds-files');
    fb.innerHTML = d.files.length ? '' : '<p class="hint">暂无文件，请上传采集数据</p>';
    d.files.forEach(f => {
      fb.insertAdjacentHTML('beforeend',
        `<div class="v-item">${catNames[f.category] || f.category} <a href="/api/datasets/files/${f.id}/download">${f.orig_name}</a>
         <span class="v-meta">${fmtSize(f.size)} · <span title="${f.sha256}">SHA✓</span> · <a href="#" onclick="App.delDsFile(${f.id});return false;">🗑</a></span></div>`);
    });
    $('ds-archive').textContent = d.status === 'archived' ? '♻ 恢复' : '🗄 归档';
    $('ds-archive').dataset.archived = d.status === 'archived' ? '1' : '';
  }

  $('ds-close').onclick = () => { $('ds-modal').style.display = 'none'; loadDatasets(); };
  $('ds-priority').onchange = async () => {
    await API.put(`/api/datasets/${dsCurrentId}/priority`, { priority: $('ds-priority').value });
    toast('优先级已更新');
  };
  $('ds-anonymize').onclick = async () => {
    const r = await API.post(`/api/datasets/${dsCurrentId}/anonymize`);
    toast(r.anonymized === 'done' ? '人脸/车牌脱敏完成' : '无视觉数据，无需脱敏');
    loadDatasetDetail();
  };
  $('ds-add-consumer').onclick = async () => {
    await API.post(`/api/datasets/${dsCurrentId}/consumers`,
      { consumer: $('ds-consumer-type').value, note: $('ds-consumer-note').value });
    $('ds-consumer-note').value = '';
    toast('血缘记录已添加'); loadDatasetDetail();
  };
  $('ds-save-tags').onclick = async () => {
    const tags = $('ds-edit-tags').value.split(/[,，]/).map(s => s.trim()).filter(Boolean);
    await API.put(`/api/datasets/${dsCurrentId}/tags`, { tags });
    toast('标签已保存'); loadDatasetDetail();
  };
  $('ds-run-qc').onclick = async () => {
    $('ds-run-qc').disabled = true;
    try {
      const r = await API.post(`/api/datasets/${dsCurrentId}/qc`);
      toast(`质检完成：${r.score} 分`);
      loadDatasetDetail();
    } catch (err) { toast(err.message, true); }
    finally { $('ds-run-qc').disabled = false; }
  };
  $('ds-manifest').onclick = () => (window.location = `/api/datasets/${dsCurrentId}/manifest`);
  $('ds-zip').onclick = () => (window.location = `/api/datasets/${dsCurrentId}/download.zip`);
  $('ds-archive').onclick = async () => {
    const restore = $('ds-archive').dataset.archived === '1';
    await API.post(`/api/datasets/${dsCurrentId}/${restore ? 'restore' : 'archive'}`);
    toast(restore ? '已恢复' : '已归档'); loadDatasetDetail();
  };

  // chunked resumable upload: init → PUT chunks (skip already-received) → complete
  async function uploadFileChunked(file, onProgress) {
    const init = await API.post(`/api/datasets/${dsCurrentId}/upload/init`,
      { orig_name: file.name, size: file.size });
    const { upload_id, chunk_size, total_chunks } = init;
    const done = new Set(init.received);
    for (let n = 0; n < total_chunks; n++) {
      if (done.has(n)) { onProgress((done.size) / total_chunks, init.resumed); continue; }
      const blob = file.slice(n * chunk_size, (n + 1) * chunk_size);
      const r = await fetch(`/api/datasets/upload/${upload_id}/chunk/${n}`, { method: 'PUT', body: blob });
      if (!r.ok) throw new Error(`分片 ${n} 上传失败（可重试续传）`);
      done.add(n);
      onProgress(done.size / total_chunks, init.resumed);
    }
    const r = await fetch(`/api/datasets/upload/${upload_id}/complete`, { method: 'POST' });
    if (!r.ok) throw new Error((await r.json()).detail || '合并失败');
    return r.json();
  }

  $('ds-upload').onclick = async () => {
    const files = [...$('ds-file-input').files];
    if (!files.length) return toast('请先选择文件（可多选）', true);
    $('ds-upload').disabled = true;
    $('ds-upload-progress').style.display = 'block';
    let okCount = 0, skipCount = 0;
    try {
      for (let i = 0; i < files.length; i++) {
        const f = files[i];
        const res = await uploadFileChunked(f, (frac, resumed) => {
          const pct = Math.round(frac * 100);
          $('ds-progress-bar').style.width = pct + '%';
          $('ds-progress-text').textContent =
            `[${i + 1}/${files.length}] ${f.name} ${pct}%${resumed ? '（断点续传）' : ''}`;
        });
        res.skipped ? skipCount++ : okCount++;
      }
      toast(`上传完成：${okCount} 个文件` + (skipCount ? `，${skipCount} 个重复已跳过` : ''));
      $('ds-file-input').value = '';
      loadDatasetDetail();
    } catch (err) { toast(err.message + '，再次点击上传可从断点续传', true); }
    finally {
      $('ds-upload').disabled = false;
      $('ds-upload-progress').style.display = 'none';
      $('ds-progress-bar').style.width = '0';
    }
  };

  // ---------- replay ----------
  window.App = {
    async checklist(tid) {
      const items = await API.get('/api/tasks/checklist_template');
      if (!confirm('出车检查单确认：\n\n' + items.map((x, i) => `${i + 1}. ${x}`).join('\n') + '\n\n以上各项均已检查通过？')) return;
      await API.post(`/api/tasks/${tid}/checklist`);
      toast('检查单已确认，可下发任务'); loadTasks();
    },
    async delDriver(id) { await API.del('/api/drivers/' + id); toast('已删除'); loadDrivers(); },
    async recollect(id) {
      try {
        const r = await API.post(`/api/datasets/${id}/recollect`);
        toast(`已生成补采任务 #${r.task_id}（高优先级）`);
        loadTasks();
      } catch (err) { toast(err.message, true); }
    },
    openDataset(id) {
      dsCurrentId = id;
      $('ds-modal').style.display = 'block';
      loadDatasetDetail();
    },
    async delDataset(id) {
      if (!confirm('删除数据包及其全部文件？')) return;
      await API.del('/api/datasets/' + id); toast('数据包已删除'); loadDatasets();
    },
    async delDsFile(id) { await API.del('/api/datasets/files/' + id); toast('文件已删除'); loadDatasetDetail(); },
    attachments(pid, name) {
      attPointId = pid;
      map.closePopup();
      $('att-title').textContent = `📎 附件 — ${name || '点位 #' + pid}`;
      $('att-modal').style.display = 'block';
      loadAttachments();
    },
    async delAtt(id) { await API.del('/api/attachments/' + id); toast('附件已删除'); loadAttachments(); },
    async delPoint(id) { await API.del('/api/points/' + id); toast('已删除'); loadPoints(); },
    async delPath(id) { await API.del('/api/paths/' + id); toast('已删除'); loadPaths(); },
    async delFence(id) { await API.del('/api/geofences/' + id); toast('已删除'); loadGeofences(); },
    async dispatch(id) {
      try { await API.post(`/api/tasks/${id}/dispatch`); toast('任务已下发'); refreshAll(); }
      catch (err) { toast(err.message, true); }
    },
    async cancelTask(id) { await API.post(`/api/tasks/${id}/cancel`); toast('任务已取消'); refreshAll(); },
    async replay(vid) {
      map.closePopup();
      const track = await API.get(`/api/tracks/${vid}?limit=300`);
      if (track.length < 2) return toast('该车辆暂无足够轨迹', true);
      clearInterval(replayTimer);
      layers.replay.clearLayers();
      const gcj = track.map(p => ll(p.lat, p.lng));
      L.polyline(gcj, { color: '#9b59b6', weight: 3, opacity: 0.7 }).addTo(layers.replay);
      const mk = L.marker(gcj[0], { icon: carIcon('collecting') }).addTo(layers.replay);
      map.fitBounds(L.latLngBounds(gcj), { padding: [40, 40] });
      let i = 0;
      $('btn-stop-replay').style.display = 'inline-block';
      replayTimer = setInterval(() => {
        if (++i >= gcj.length) { clearInterval(replayTimer); toast('回放结束'); return; }
        mk.setLatLng(gcj[i]);
      }, 60);
    },
  };
  $('btn-stop-replay').onclick = () => {
    clearInterval(replayTimer);
    layers.replay.clearLayers();
    $('btn-stop-replay').style.display = 'none';
  };

  // ---------- task creation ----------
  $('btn-create-task').onclick = async () => {
    const name = $('task-name').value.trim();
    if (!name) return toast('请输入任务名称', true);
    try {
      await API.post('/api/tasks', {
        name,
        vehicle_id: $('task-vehicle').value ? +$('task-vehicle').value : null,
        path_id: $('task-path').value ? +$('task-path').value : null,
        priority: $('task-priority').value,
        driver_id: $('task-driver').value ? +$('task-driver').value : null,
        sensor_config_id: $('task-sensor').value ? +$('task-sensor').value : null,
        event_rules: parseEventRules($('task-event-rules').value),
      });
      $('task-name').value = '';
      $('task-event-rules').value = '';
      toast('任务已创建');
      loadTasks();
    } catch (err) { toast(err.message, true); }
  };

  // ---------- panels / export ----------
  document.querySelectorAll('.tab').forEach(t => {
    t.onclick = () => {
      document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
      document.querySelectorAll('.panel').forEach(x => (x.style.display = 'none'));
      t.classList.add('active');
      $(t.dataset.panel).style.display = 'block';
      if (t.dataset.panel === 'panel-alerts') { loadAlerts(); API.post('/api/alerts/read_all'); }
      if (t.dataset.panel === 'panel-data') loadDatasets();
      if (t.dataset.panel === 'panel-report') loadReports();
      if (t.dataset.panel === 'panel-vehicles') { loadDrivers(); loadStorage(); }
    };
  });
  $('btn-export-csv').onclick = () => (window.location = '/api/export/points.csv');
  $('btn-export-geojson').onclick = () => (window.location = '/api/export/geojson');

  // ---------- refresh loop ----------
  function refreshAll() { loadPoints(); loadPaths(); loadVehicles(); loadGeofences(); loadTasks(); loadStats(); loadDrivers(); loadSensorConfigs(); loadStorage(); }
  refreshAll();
  setInterval(() => {
    loadVehicles(); loadTasks(); loadStats();
    if (heatOn) API.get('/api/heatmap').then(setHeat);
    if ($('panel-data').style.display !== 'none') loadDatasets();
  }, 4000);
})();
