'use strict';

// ------------------------------------------------------------------ basics

const TOKEN = document.querySelector('meta[name="omni-token"]').content;
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const state = {
  snap: null,
  live: [],
  settings: null,
  schema: null,
  view: 'overview',
  charts: {},
  built: {},
  procSort: 'cpu',
  historyRange: '1h',
  stressTarget: 'cpu',
};

async function api(path, options = {}) {
  const init = { ...options, headers: { 'X-OMNI-Token': TOKEN, ...(options.headers || {}) } };
  if (options.body !== undefined && typeof options.body !== 'string') {
    init.body = JSON.stringify(options.body);
    init.headers['Content-Type'] = 'application/json';
  }
  const res = await fetch(path, init);
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!res.ok) throw Object.assign(new Error((data && data.detail) || `Request failed (${res.status})`), { status: res.status });
  return data;
}

let toastTimer;
function toast(message) {
  const node = $('#toast');
  node.textContent = message;
  node.hidden = false;
  node.classList.remove('show');
  void node.offsetWidth; // restart the entrance animation
  node.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { node.hidden = true; }, 4000);
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === 'class') node.className = value;
    else if (key === 'style') node.style.cssText = value; // CSSOM: allowed by the page's CSP, unlike style=""
    else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else if (value === true) node.setAttribute(key, '');
    else if (value !== false && value != null) node.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child == null || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function kv(container, rows) {
  container.replaceChildren(...rows.filter(Boolean).map(([k, v]) => el('div', {}, el('dt', {}, k), el('dd', {}, v ?? '—'))));
}

// ------------------------------------------------------------------ units (metric by default)

const unit = (key) => (state.settings ? state.settings.units[key] : { temperature: 'C', data: 'si', energy: 'Wh' }[key]);
const fmt = {
  temp(c, digits = 0) {
    if (c == null) return '—';
    return unit('temperature') === 'F' ? `${(c * 9 / 5 + 32).toFixed(digits)} °F` : `${c.toFixed(digits)} °C`;
  },
  tempValue(c) { return c == null ? null : unit('temperature') === 'F' ? c * 9 / 5 + 32 : c; },
  tempUnit() { return unit('temperature') === 'F' ? '°F' : '°C'; },
  tempAxis(v) { return `${v.toFixed(0)}°`; },
  bytes(n, digits = 1) {
    if (n == null) return '—';
    const iec = unit('data') === 'iec';
    const base = iec ? 1024 : 1000;
    const names = iec ? ['B', 'KiB', 'MiB', 'GiB', 'TiB'] : ['B', 'kB', 'MB', 'GB', 'TB'];
    let i = 0, v = Math.abs(n);
    while (v >= base && i < names.length - 1) { v /= base; i++; }
    return `${n < 0 ? '-' : ''}${i === 0 ? v.toFixed(0) : v.toFixed(digits)} ${names[i]}`;
  },
  rate(n) { return n == null ? '—' : `${fmt.bytes(n)}/s`; },
  energy(mwh) {
    if (mwh == null) return '—';
    return unit('energy') === 'mWh' ? `${Math.round(mwh).toLocaleString()} mWh` : `${(mwh / 1000).toFixed(1)} Wh`;
  },
  wh(wh) { return wh == null ? '—' : unit('energy') === 'mWh' ? `${Math.round(wh * 1000).toLocaleString()} mWh` : `${wh.toFixed(wh < 1 ? 3 : 2)} Wh`; },
  watts(w, digits = 1) { return w == null ? '—' : `${w.toFixed(digits)} W`; },
  freq(mhz) { return mhz == null ? '—' : mhz >= 1000 ? `${(mhz / 1000).toFixed(2)} GHz` : `${Math.round(mhz)} MHz`; },
  pct(v, digits = 0) { return v == null ? '—' : `${v.toFixed(digits)} %`; },
  rpm(v) { return v == null ? '—' : `${Math.round(v).toLocaleString()} RPM`; },
  duration(s) {
    if (s == null || s < 0) return '—';
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
    return h ? `${h} h ${String(m).padStart(2, '0')} min` : `${m} min`;
  },
  time(ts) { return new Date(ts * 1000).toLocaleString(); },
};
const num = (digits = 0) => (v) => (v == null ? '—' : v.toFixed(digits));
const wattsAxis = (v) => `${Math.abs(v) < 10 && v % 1 ? v.toFixed(1) : v.toFixed(0)} W`;
const pctAxis = (v) => `${v.toFixed(0)}%`;

// ------------------------------------------------------------------ charts

const CHARTS = {
  usage: [[['cpu_pct', 'CPU'], ['gpu_pct', 'GPU'], ['ram_pct', 'Memory']], { min: 0, max: 100, format: pctAxis }],
  temps: [[['cpu_temp_c', 'CPU'], ['gpu_temp_c', 'GPU']], { format: fmt.tempAxis, temp: true }],
  'ov-power': [[['cpu_w', 'CPU package'], ['gpu_w', 'Dedicated GPU'], ['system_w', 'Whole laptop (battery)']], { min: 0, format: wattsAxis }],
  'cpu-usage': [[['cpu_pct', 'CPU']], { min: 0, max: 100, format: pctAxis }],
  'cpu-clock': [[['cpu_mhz', 'Clock']], { format: (v) => `${(v / 1000).toFixed(1)} GHz` }],
  'cpu-temp': [[['cpu_temp_c', 'CPU']], { format: fmt.tempAxis, temp: true }],
  'cpu-power': [[['cpu_w', 'Package'], ['cpu_cores_w', 'Cores'], ['igpu_w', 'Integrated GPU']], { min: 0, format: wattsAxis }],
  'gpu-usage': [[['gpu_pct', 'GPU']], { min: 0, max: 100, format: pctAxis }],
  'gpu-power': [[['gpu_w', 'Power']], { min: 0, format: wattsAxis, emptyText: 'No power readings: the GPU is asleep (≈0 W) or paused on battery.' }],
  'gpu-temp': [[['gpu_temp_c', 'GPU']], { format: fmt.tempAxis, temp: true }],
  energy: [[['cpu_w', 'CPU package'], ['cpu_cores_w', 'CPU cores'], ['igpu_w', 'Integrated GPU'], ['gpu_w', 'Dedicated GPU']], { min: 0, format: wattsAxis }],
  'batt-pct': [[['batt_pct', 'Charge']], { min: 0, max: 100, format: pctAxis }],
  'batt-power': [[['batt_w', 'Battery power']], { signed: true, format: wattsAxis }],
  'batt-capacity': [[['full_wh', 'Full charge'], ['design_wh', 'Design']], {
    min: 0, format: (v) => `${v.toFixed(0)} Wh`, emptyText: 'Needs readings from at least two days. OMNI records one per day.' }],
  'disk-io': [[['disk_read_bps', 'Read'], ['disk_write_bps', 'Write']], { min: 0, format: (v) => fmt.rate(v) }],
  'net-io': [[['net_down_bps', 'Download'], ['net_up_bps', 'Upload']], { min: 0, format: (v) => fmt.rate(v) }],
  stress: [[['cpu_temp_c', 'CPU'], ['gpu_temp_c', 'GPU']], { format: fmt.tempAxis, temp: true }],
  'h-usage': [[['cpu_pct', 'CPU'], ['gpu_pct', 'GPU'], ['ram_pct', 'Memory']], { min: 0, max: 100, format: pctAxis }],
  'h-temps': [[['cpu_temp_c', 'CPU'], ['gpu_temp_c', 'GPU']], { format: fmt.tempAxis, temp: true }],
  'h-power': [[['cpu_w', 'CPU package'], ['gpu_w', 'Dedicated GPU'], ['system_w', 'Whole laptop (battery)']], { min: 0, format: wattsAxis }],
  'h-fans': [[['fan_rpm', 'Fastest fan']], { min: 0, format: (v) => `${(v / 1000).toFixed(1)}k` , emptyText: 'No fan readings recorded yet.' }],
  'h-batt': [[['batt_pct', 'Charge']], { min: 0, max: 100, format: pctAxis }],
  'speed-history': [[['download_mbps', 'Download'], ['upload_mbps', 'Upload']], { min: 0, format: (v) => `${v.toFixed(0)} Mbps`, emptyText: 'Run two or more speed tests to see a trend.' }],
  'h-gpu-power': [[['gpu_w', 'Power']], { min: 0, format: wattsAxis }],
};

// Charts fed from the live stream scroll in a fixed window; the rest fit their own data.
const FITTED_CHARTS = new Set(['batt-capacity', 'stress', 'speed-history']);

function chart(name) {
  if (!state.charts[name]) {
    const [series, opts] = CHARTS[name];
    const node = document.querySelector(`[data-chart="${name}"]`);
    const live = !name.startsWith('h-') && !FITTED_CHARTS.has(name);
    state.charts[name] = new Charts.LineChart(node, series.map(([key, label], i) => ({ key, label, color: `--series-${i + 1}` })),
      { ...opts, live, area: series.length === 1 });
  }
  return state.charts[name];
}

function setChart(name, points) {
  const [series, opts] = CHARTS[name];
  if (opts.temp && unit('temperature') === 'F') {
    points = points.map((p) => {
      const copy = { ...p };
      for (const [key] of series) copy[key] = fmt.tempValue(p[key]);
      return copy;
    });
  }
  chart(name).setData(points);
}

function compact(snap) {
  const b = snap.battery || {}, g = snap.gpu || {}, s = snap.storage || {}, n = snap.network || {}, p = snap.power || {};
  const fans = (snap.fans || []).map((f) => f.rpm).filter((v) => v != null);
  // Windows' counter keeps reporting dGPU load while NVML is paused or the GPU sleeps.
  const dgpu = dedicatedGpu(snap);
  const point = {
    ts: snap.ts, cpu_pct: snap.cpu.usage_pct, cpu_temp_c: snap.cpu.temp_c, cpu_mhz: snap.cpu.clock_mhz,
    ram_pct: snap.memory.usage_pct, gpu_pct: g.util_pct ?? dgpu?.load_pct ?? null, gpu_temp_c: g.temp_c, gpu_w: g.power_w,
    batt_pct: b.percent, batt_w: b.power_w, disk_read_bps: s.read_bps, disk_write_bps: s.write_bps,
    net_down_bps: n.down_bps, net_up_bps: n.up_bps, cpu_w: p.cpu_package_w, cpu_cores_w: p.cpu_cores_w,
    igpu_w: p.igpu_w, system_w: p.system_w, fan_rpm: fans.length ? Math.max(...fans) : null,
  };
  (snap.fans || []).slice(0, 4).forEach((f, i) => { point[`fan_${i}`] = f.rpm; });
  return point;
}

// ------------------------------------------------------------------ live connection

let socket, retry = 1000;
function connect() {
  const conn = $('#conn');
  socket = new WebSocket(`ws://${location.host}/ws/live?token=${encodeURIComponent(TOKEN)}`);
  socket.onopen = () => { retry = 1000; conn.className = 'conn ok'; $('#conn-text').textContent = 'Live'; };
  socket.onmessage = (event) => {
    const snap = JSON.parse(event.data);
    state.snap = snap;
    const last = state.live[state.live.length - 1];
    if (!last || last.ts !== snap.ts) state.live.push(compact(snap));
    const cutoff = snap.ts - 960; // keep a little more than the longest chart window (15 min)
    while (state.live.length && state.live[0].ts < cutoff) state.live.shift();
    render();
  };
  socket.onclose = () => {
    conn.className = 'conn bad';
    $('#conn-text').textContent = 'Reconnecting';
    setTimeout(connect, retry);
    retry = Math.min(retry * 2, 15000);
  };
}

// ------------------------------------------------------------------ navigation

function showView(name) {
  const mini = name === 'mini';
  document.body.classList.toggle('mini', mini);
  if (mini) name = 'overview';
  if (!$(`#view-${name}`)) name = 'overview';
  state.view = name;
  $$('.view').forEach((v) => v.classList.toggle('active', v.id === `view-${name}`));
  $$('.sidebar button').forEach((b) => b.classList.toggle('active', b.dataset.view === name));
  $('#sidebar').classList.remove('open');
  const hash = mini ? 'mini' : name;
  if (location.hash !== `#${hash}`) history.replaceState(null, '', `#${hash}`);
  VIEWS[name]?.show?.();
  render();
}

// ------------------------------------------------------------------ rendering

function render() {
  const snap = state.snap;
  if (!snap) return;
  renderAlerts(snap.alerts || []);
  renderTopbar(snap);
  const view = VIEWS[state.view];
  if (!view?.update) return;
  if (view.build && state.built[state.view] !== view.key?.(snap)) {
    view.build(snap);
    state.built[state.view] = view.key?.(snap);
  }
  view.update(snap);
}

function renderAlerts(alerts) {
  const box = $('#alerts');
  const key = alerts.map((a) => a.key + a.message).join('|');
  if (box.dataset.key === key) return;
  box.dataset.key = key;
  box.replaceChildren(...alerts.map((a) => el('div', { class: `alert ${a.level}` },
    el('span', { class: 'icon', 'aria-hidden': 'true' }, a.level === 'info' ? 'ℹ' : '⚠'),
    el('span', {}, a.message))));
}

const topbarChips = {};
function renderTopbar(snap) {
  const p = snap.power || {};
  const fans = (snap.fans || []).map((f) => f.rpm).filter((v) => v != null);
  const chips = [
    ['cpu', 'cpu', snap.cpu.usage_pct, (v) => fmt.pct(v)],
    ['temp', 'thermo', fmt.tempValue(snap.cpu.temp_c), (v) => (v == null ? '—' : `${Math.round(v)}${fmt.tempUnit()}`)],
    ['watts', 'bolt', p.cpu_package_w, (v) => fmt.watts(v, 0)],
    ['fan', 'fan', fans.length ? Math.max(...fans) : null, (v) => fmt.rpm(v)],
  ];
  const box = $('#topbar-stats');
  for (const [key, icon, value, format] of chips) {
    if (!topbarChips[key]) {
      const valueNode = el('span');
      topbarChips[key] = { node: el('span', { class: 'chip', 'data-icon': icon, 'data-size': 14 }, valueNode), valueNode };
      box.append(topbarChips[key].node);
      Icons.hydrate(topbarChips[key].node.parentElement);
    }
    topbarChips[key].node.hidden = value == null;
    UI.tween(topbarChips[key].valueNode, value, format, 400);
  }
}

function dedicatedGpu(snap) {
  return (snap.gpus || []).find((g) => !g.integrated) || (snap.gpus || [])[0] || null;
}

function maxFanRpm() { return state.settings?.display.fan_max_rpm || 6000; }

let ovGauges = null;
const CAP_LABELS = { ok: 'Works', partly: 'Partly', needs: 'Needs setup', none: 'Not available' };

function capRow(name, status, detail, how) {
  return el('div', { class: 'cap', 'data-status': status },
    el('span', { class: 'cap-dot', title: CAP_LABELS[status] || status }),
    el('b', {}, name),
    el('div', { class: 'cap-detail' }, detail || ''),
    how ? el('div', { class: 'cap-how' }, how) : '');
}

async function loadDevice() {
  const info = await api('/api/device');
  const d = info.device;
  const title = [d.vendor !== d.line ? d.vendor : null, d.model].filter(Boolean).join(' ') || 'This PC';
  $('#device-name').textContent = title;
  $('#device-sub').textContent = [d.form_factor, d.line && `${d.line} series`, d.model_code, d.bios && `BIOS ${d.bios}`]
    .filter(Boolean).join(' · ') || (info.ready ? '' : 'Reading the firmware…');
  const counts = {};
  info.capabilities.forEach((c) => { counts[c.status] = (counts[c.status] || 0) + 1; });
  $('#device-summary').replaceChildren(...['ok', 'partly', 'needs', 'none'].filter((k) => counts[k])
    .map((k) => el('span', { class: `badge${k === 'ok' ? ' good' : ''}` }, `${counts[k]} ${CAP_LABELS[k].toLowerCase()}`)));
  $('#cap-list').replaceChildren(...info.capabilities.map((c) => capRow(c.area, c.status, c.detail, c.how)));
  $('#provider-list').replaceChildren(...Object.entries(info.providers).map(([name, status]) => {
    const kind = /fan\(s\)|reports fans/.test(status) ? 'ok' : /admin|running$/.test(status) && !/not running/.test(status) ? 'partly' : 'none';
    return capRow(name, kind, status);
  }));
  $('#vendor-tools').replaceChildren(...(info.tools.length ? [el('div', { class: 'vendor-tools' },
    el('h3', {}, `What you can change on this ${d.vendor} and where`),
    el('div', { class: 'cap-list' }, ...info.tools.map((t) => capRow(t.what, 'partly', t.how))))] : []));
}

// ------------------------------------------------------------------ health summary

/** One plain-language verdict for the whole machine, so the first screen answers "is anything wrong?" */
function assess(snap) {
  const issues = [];
  const add = (level, text, view) => issues.push({ level, text, view });
  const limits = state.settings?.alerts || {};
  const cpuLimit = limits.cpu_temp_c ?? 95;
  const gpuLimit = limits.gpu_temp_c ?? 87;
  const { cpu, memory: m, battery: b } = snap;

  if (cpu.temp_c >= cpuLimit) add('danger', `CPU is very hot (${fmt.temp(cpu.temp_c)})`, 'cpu');
  else if (cpu.temp_c >= cpuLimit - 10) add('warn', `CPU is running warm (${fmt.temp(cpu.temp_c)})`, 'cpu');
  const gpuTemp = snap.gpu?.available ? snap.gpu.temp_c : null;
  if (gpuTemp >= gpuLimit) add('danger', `GPU is very hot (${fmt.temp(gpuTemp)})`, 'gpu');
  else if (gpuTemp >= gpuLimit - 7) add('warn', `GPU is running warm (${fmt.temp(gpuTemp)})`, 'gpu');
  if (m.usage_pct >= 90) add('warn', `Memory is almost full (${Math.round(m.usage_pct)} %)`, 'storage');
  if (b.present) {
    if (b.health_pct != null && b.health_pct < 70) add('danger', `Battery has lost ${(100 - b.health_pct).toFixed(0)} % of its capacity`, 'battery');
    else if (b.health_pct != null && b.health_pct < 85) add('warn', `Battery health is ${b.health_pct.toFixed(0)} %`, 'battery');
    if (b.state === 'discharging' && b.percent <= (limits.battery_low_pct ?? 20)) add('danger', `Battery is low (${Math.round(b.percent)} %)`, 'battery');
  }
  for (const d of snap.storage?.disks || []) {
    if (d.health && d.health !== 'Healthy') add('danger', `Drive ${d.name} reports "${d.health}"`, 'storage');
  }
  return issues;
}

function renderHealth(snap) {
  const box = $('#health-summary');
  const issues = assess(snap);
  const level = issues.some((i) => i.level === 'danger') ? 'danger' : issues.length ? 'warn' : 'ok';
  const key = level + issues.map((i) => i.text).join('|');
  if (box.dataset.key === key) return;
  box.dataset.key = key;
  box.dataset.level = level;
  const headline = { ok: 'Everything looks healthy', warn: 'Worth a look', danger: 'Needs attention' }[level];
  const mark = { ok: '✓', warn: '▲', danger: '✖' }[level];
  box.replaceChildren(
    el('span', { class: 'health-mark', 'aria-hidden': 'true' }, mark),
    el('div', { class: 'health-body' },
      el('strong', {}, headline),
      issues.length
        ? el('ul', {}, ...issues.map((i) => el('li', {}, el('button', { type: 'button', class: 'link', onclick: () => showView(i.view) }, i.text))))
        : el('span', { class: 'hint' }, 'Temperatures, memory, battery and drives are all in their normal range.')));
}

const VIEWS = {
  overview: {
    key: () => 'static',
    build(snap) {
      const go = (view) => () => showView(view);
      ovGauges = {
        cpu: new UI.Gauge({ label: 'CPU load', unit: '%', max: 100, format: num(0), warn: 85, onClick: go('cpu') }),
        cpuTemp: new UI.Gauge({ label: 'CPU temperature', unit: fmt.tempUnit(), max: 105, format: num(0), warn: 85, danger: 95, onClick: go('cpu') }),
        cpuW: new UI.Gauge({ label: 'CPU power', unit: 'W', max: 60, format: num(1), onClick: go('energy') }),
        gpu: new UI.Gauge({ label: 'GPU load', unit: '%', max: 100, format: num(0), warn: 85, onClick: go('gpu') }),
        gpuTemp: new UI.Gauge({ label: 'GPU temperature', unit: fmt.tempUnit(), max: 100, format: num(0), warn: 80, danger: 87, onClick: go('gpu') }),
        mem: new UI.Gauge({ label: 'Memory', unit: '%', max: 100, format: num(0), warn: 85, onClick: go('storage') }),
        fan: new UI.Gauge({ label: 'Fans', unit: 'RPM', max: maxFanRpm(), format: (v) => (v == null ? '—' : Math.round(v).toLocaleString()), onClick: go('fans') }),
        batt: new UI.Gauge({ label: 'Battery', unit: '%', max: 100, format: num(0), onClick: go('battery') }),
      };
      $('#overview-gauges').replaceChildren(...Object.values(ovGauges).map((g) => g.el));
      $('#overview-io').replaceChildren(...['Disk read', 'Disk write', 'Download', 'Upload'].map((label) =>
        el('div', { class: 'mini-stat' }, el('div', { class: 'mini-label' }, label), el('div', { class: 'mini-value' }, '—'))));
      this.cpuWMax = 60;
    },
    update(snap) {
      const { cpu, battery: b, memory: m, storage: s, network: n, power: p = {} } = snap;
      const g = dedicatedGpu(snap);
      $('#overview-machine').textContent = [cpu.name, g && g.name].filter(Boolean).join(' · ');
      renderHealth(snap);
      ovGauges.cpu.set(cpu.usage_pct, fmt.freq(cpu.clock_mhz));
      ovGauges.cpuTemp.set(fmt.tempValue(cpu.temp_c), cpu.temp_c >= 95 ? 'Very hot' : cpu.temp_c >= 85 ? 'Hot' : 'Normal');
      // Grow the power gauge's scale to the highest reading seen, rounded to a tidy number.
      if ((p.cpu_package_w || 0) > this.cpuWMax) this.cpuWMax = Math.ceil(p.cpu_package_w / 20) * 20;
      ovGauges.cpuW.set(p.cpu_package_w, p.cpu_cores_w != null ? `Cores ${fmt.watts(p.cpu_cores_w)}` : 'Not reported', this.cpuWMax);
      if (!g) ovGauges.gpu.set(null, 'No GPU found');
      else if (g.asleep) ovGauges.gpu.set(0, 'Sleeping');
      else ovGauges.gpu.set(g.load_pct, g.power_w != null ? fmt.watts(g.power_w) : g.name.replace(/\(R\)|NVIDIA GeForce |Laptop GPU/g, '').trim());
      const gpuTemp = snap.gpu.available ? snap.gpu.temp_c : null;
      ovGauges.gpuTemp.set(fmt.tempValue(gpuTemp), snap.gpu.paused ? 'Paused on battery' : gpuTemp == null ? 'Not reported' : 'Dedicated GPU');
      ovGauges.mem.set(m.usage_pct, `${fmt.bytes(m.used)} of ${fmt.bytes(m.total)}`);
      const fans = (snap.fans || []).filter((f) => f.rpm != null);
      if (fans.length) ovGauges.fan.set(Math.max(...fans.map((f) => f.rpm)), `${fans.length} fan${fans.length > 1 ? 's' : ''}`, maxFanRpm());
      else ovGauges.fan.set(null, 'No fan sensor');
      if (b.present) ovGauges.batt.set(b.percent, b.state === 'discharging' && b.time_left_s ? `${fmt.duration(b.time_left_s)} left` : b.state);
      else ovGauges.batt.set(null, 'No battery');
      const io = $$('#overview-io .mini-value');
      [s.read_bps, s.write_bps, n.down_bps, n.up_bps].forEach((v, i) => UI.tween(io[i], v, fmt.rate));
      setChart('usage', state.live);
      setChart('temps', state.live);
      setChart('ov-power', state.live);
    },
  },

  cpu: {
    key: (snap) => `${snap.cpu.threads}`,
    build(snap) {
      const c = snap.cpu;
      this.gauges = {
        load: new UI.Gauge({ label: 'Load', unit: '%', max: 100, format: num(0), warn: 85 }),
        clock: new UI.Gauge({ label: c.hybrid ? 'P-core clock' : 'Clock', unit: 'GHz', max: 6000, format: (v) => (v == null ? '—' : (v / 1000).toFixed(2)) }),
        temp: new UI.Gauge({ label: 'Temperature', unit: fmt.tempUnit(), max: 105, format: num(0), warn: 85, danger: 95 }),
        power: new UI.Gauge({ label: 'Package power', unit: 'W', max: 60, format: num(1) }),
      };
      this.powerMax = 60;
      $('#cpu-gauges').replaceChildren(...Object.values(this.gauges).map((g) => g.el));
      this.cyl = [];
      const groups = c.hybrid
        ? [['P', `Performance cores · ${c.p_cores} cores, ${c.per_thread.filter((t) => t.type === 'P').length} threads`],
          ['E', `Efficiency cores · ${c.e_cores} cores, ${c.per_thread.filter((t) => t.type === 'E').length} threads`]]
        : [[null, `${c.cores} cores · ${c.threads} threads`]];
      $('#cpu-cylinders').replaceChildren(...groups.map(([type, title]) => {
        const row = el('div', { class: 'cyl-row' });
        for (const t of c.per_thread.filter((x) => x.type === type)) {
          const cyl = new UI.Cylinder(`#${t.id}`, type === 'E' ? 'e-core' : type === 'P' ? 'p-core' : '');
          this.cyl[t.id] = cyl;
          row.append(cyl.el);
        }
        return el('div', { class: 'cyl-group' }, el('h3', {}, title), row);
      }));
      $('#cpu-thread-hint').textContent = c.hybrid
        ? 'Fill = load. Blue = performance cores, aqua = efficiency cores; the fill turns amber above 60 % and orange above 90 %. Windows does not report an E-core base clock, so E-cores show speed relative to their base (×) instead of MHz.'
        : 'Fill = load (amber above 60 %, orange above 90 %), caption = live clock.';
    },
    update(snap) {
      const c = snap.cpu, p = snap.power || {};
      $('#cpu-name').textContent = c.name;
      this.gauges.load.set(c.usage_pct, `${c.cores} cores · ${c.threads} threads`);
      this.gauges.clock.set(c.clock_mhz, `Base ${fmt.freq(c.base_mhz)}`);
      this.gauges.temp.set(fmt.tempValue(c.temp_c), Object.keys(c.zones || {}).length ? `${Object.keys(c.zones).length} sensor(s)` : 'Not reported');
      if ((p.cpu_package_w || 0) > this.powerMax) this.powerMax = Math.ceil(p.cpu_package_w / 20) * 20;
      this.gauges.power.set(p.cpu_package_w, p.cpu_package_w == null ? 'Not reported by Windows' : `Cores ${fmt.watts(p.cpu_cores_w)}`, this.powerMax);
      for (const t of c.per_thread) {
        const caption = t.clock_mhz ? `${(t.clock_mhz / 1000).toFixed(1)} GHz` : t.perf_pct ? `×${(t.perf_pct / 100).toFixed(1)}` : '';
        this.cyl[t.id]?.set(t.load_pct, caption, `Thread ${t.id}${t.type ? ` (${t.type}-core ${t.core})` : ''}: ${fmt.pct(t.load_pct)} load`);
      }
      kv($('#cpu-kv'), [
        ['Processor', c.name], ['Cores / threads', `${c.cores} / ${c.threads}`],
        c.hybrid ? ['Core types', `${c.p_cores} performance + ${c.e_cores} efficiency`] : null,
        ['Base clock', fmt.freq(c.base_mhz)], ['Package power', fmt.watts(p.cpu_package_w)],
        ['Core power', fmt.watts(p.cpu_cores_w)], ['Integrated GPU power', fmt.watts(p.igpu_w)],
        ...Object.entries(c.zones || {}).map(([zone, t]) => [`Sensor ${zone}`, fmt.temp(t, 1)]),
      ]);
      setChart('cpu-usage', state.live);
      setChart('cpu-clock', state.live);
      setChart('cpu-temp', state.live);
      setChart('cpu-power', state.live);
    },
  },

  gpu: {
    key: (snap) => (snap.gpus || []).map((g) => g.luid).join(','),
    build(snap) {
      this.cards = {};
      const gpus = snap.gpus || [];
      if (!gpus.length) {
        $('#gpu-cards').replaceChildren(el('div', { class: 'card' }, 'No graphics adapters reported by Windows.'));
        return;
      }
      $('#gpu-cards').replaceChildren(...gpus.map((g) => {
        const gauge = new UI.Gauge({ label: 'Load', unit: '%', max: 100, format: num(0), warn: 90 });
        const info = el('div', { class: 'kv compact' });
        const engines = el('div', { class: 'engines' });
        this.cards[g.luid] = { gauge, info, engines };
        return el('div', { class: 'card gpu-card' },
          el('div', { class: 'gpu-head' }, el('span', { class: `vendor ${g.vendor.toLowerCase()}` }, g.vendor),
            el('h2', {}, g.name), el('span', { class: 'badge' }, g.integrated ? 'Integrated' : 'Dedicated')),
          el('div', { class: 'gpu-body' }, gauge.el, el('div', { class: 'gpu-detail' }, info, engines)));
      }));
    },
    update(snap) {
      for (const g of snap.gpus || []) {
        const card = this.cards?.[g.luid];
        if (!card) continue;
        card.gauge.set(g.asleep ? 0 : g.load_pct, g.asleep ? 'Sleeping' : g.limits?.length ? `Limited: ${g.limits.join(', ')}` : '');
        kv(card.info, [
          g.temp_c != null ? ['Temperature', fmt.temp(g.temp_c)] : null,
          g.power_w != null ? ['Power', `${fmt.watts(g.power_w)}${g.power_limit_w ? ` of ${fmt.watts(g.power_limit_w, 0)}` : ''}`] : null,
          g.clock_mhz ? ['Core clock', fmt.freq(g.clock_mhz)] : null,
          g.dedicated_total ? ['Dedicated memory', `${fmt.bytes(g.dedicated_used || 0)} of ${fmt.bytes(g.dedicated_total)}`] : null,
          ['Shared memory used', fmt.bytes(g.shared_used || 0)],
        ]);
        card.engines.replaceChildren(...Object.entries(g.engines || {}).map(([name, v]) => el('div', { class: 'engine' },
          el('span', {}, name.replace(/_\d+$/, '')), el('div', { class: 'bar' }, el('span', { style: `width:${v}%` })), el('b', {}, `${v.toFixed(0)}%`))));
      }
      setChart('gpu-usage', state.live);
      setChart('gpu-power', state.live);
      setChart('gpu-temp', state.live);
    },
  },

  energy: {
    key: () => 'static',
    build() {
      this.donut = new UI.Donut('total');
      $('#energy-donut').replaceChildren(this.donut.el);
    },
    update(snap) {
      const p = snap.power || {};
      const pkg = p.cpu_package_w || 0, cores = p.cpu_cores_w || 0, igpu = p.igpu_w || 0;
      const uncore = Math.max(0, pkg - cores - igpu);
      const parts = [
        { key: 'cores', label: 'CPU cores', value: cores, color: '--series-1' },
        { key: 'igpu', label: 'Integrated GPU', value: igpu, color: '--series-2' },
        { key: 'uncore', label: 'CPU other (cache, memory controller)', value: uncore, color: '--series-3' },
        { key: 'dgpu', label: 'Dedicated GPU', value: p.dgpu_w || 0, color: '--series-4' },
      ];
      if (p.rest_w != null) parts.push({ key: 'rest', label: 'Rest of laptop (screen, SSD, Wi-Fi…)', value: p.rest_w, color: '--series-5' });
      for (const part of parts) part.text = fmt.watts(part.value);
      const total = p.system_w ?? parts.reduce((s, x) => s + x.value, 0);
      this.donut.set(parts, fmt.watts(total));
      kv($('#energy-kv'), [
        ['CPU package', fmt.watts(p.cpu_package_w)], ['CPU cores', fmt.watts(p.cpu_cores_w)],
        ['Integrated GPU', fmt.watts(p.igpu_w)], ['Memory (DRAM)', p.dram_w != null ? fmt.watts(p.dram_w) : 'Not reported'],
        ['Dedicated GPU', p.dgpu_w != null ? fmt.watts(p.dgpu_w) : snap.gpu.asleep ? 'Sleeping (≈0 W)' : snap.gpu.paused ? 'Paused on battery' : '—'],
        ['Whole laptop', p.system_w != null ? fmt.watts(p.system_w) : 'Only measurable on battery'],
      ]);
      $('#energy-note').textContent = p.system_w == null
        ? 'Unplug the charger to see the whole laptop\'s draw (Windows measures it from the battery\'s discharge rate).'
        : `Battery is supplying ${fmt.watts(p.system_w)}. "Rest of laptop" = that minus the measured CPU and GPU.`;
      const rows = [['cpu_package_w', 'CPU package'], ['cpu_cores_w', 'CPU cores'], ['igpu_w', 'Integrated GPU'],
        ['dgpu_w', 'Dedicated GPU'], ['system_w', 'Whole laptop (on battery)']];
      $('#energy-table').replaceChildren(el('tr', {}, el('th', {}, 'Component'), el('th', { class: 'num' }, 'Energy')),
        ...rows.map(([key, label]) => el('tr', {}, el('td', {}, label), el('td', { class: 'num' }, fmt.wh(p.energy_wh?.[key])))));
      $('#energy-since').textContent = p.energy_since ? `Counting since ${fmt.time(p.energy_since)}` : '';
      setChart('energy', state.live);
    },
  },

  fans: {
    key: (snap) => (snap.fans || []).map((f) => `${f.source}:${f.hardware}:${f.name}`).join('|'),
    build(snap) {
      this.fans = [];
      const fans = snap.fans || [];
      $('#fan-cards').replaceChildren(...(fans.length ? fans.map((f, i) => {
        const fan = new UI.Fan(f.name);
        this.fans[i] = fan;
        return fan.el;
      }) : [el('div', { class: 'card empty-state' }, el('div', { class: 'empty-icon' }, '✲'), el('div', {}, 'No fan speed sensors found on this PC yet.'))]));
      // Fan chart: one series per fan (up to 4), keyed fan_0..fan_3 in the live buffer.
      CHARTS.fans = [fans.slice(0, 4).map((f, i) => [`fan_${i}`, f.name]), { min: 0, format: (v) => `${(v / 1000).toFixed(1)}k`, emptyText: 'No fan readings yet.' }];
      const host = $('[data-chart="fans"]');
      host.parentElement.querySelectorAll('.legend').forEach((n) => n.remove());
      delete state.charts.fans;
    },
    update(snap) {
      const fans = snap.fans || [];
      if (!this.deviceAt || Date.now() - this.deviceAt > 15000) {
        this.deviceAt = Date.now();
        loadDevice().catch(() => { this.deviceAt = 0; });
      }
      fans.forEach((f, i) => this.fans[i]?.set(f.rpm, maxFanRpm(), `${f.source}${f.hardware && f.hardware !== 'Laptop' ? ` · ${f.hardware}` : ''}`));
      const sources = (snap.sources || []).join(' · ');
      $('#sensor-sources').textContent = sources ? `Sources: ${sources}` : 'No extra sensor sources found.';
      const bridged = (snap.sources || []).some((s) => !s.startsWith('HP BIOS'));
      $('#sensor-help').hidden = fans.length > 0 && bridged;
      $('#sensor-help h2').textContent = fans.length ? 'Want more sensors?' : 'Not seeing your fans?';
      const control = snap.fan_control;
      $('#fan-control-card').hidden = !control;
      if (control) $('#max-fan').checked = !!control.max_fan;
      renderSensorTable(snap.sensors || []);
      if (fans.length) setChart('fans', state.live);
    },
  },

  battery: {
    key: (snap) => String(!!snap.battery.present),
    build(snap) {
      if (!snap.battery.present) {
        $('#battery-hero').replaceChildren(el('div', { class: 'empty-state' }, el('div', { class: 'empty-icon' }, '▮'), 'No battery detected. This looks like a desktop.'));
        this.cell = null;
        return;
      }
      this.cell = new UI.BatteryCell();
      this.text = el('div', { class: 'battery-text' });
      $('#battery-hero').replaceChildren(this.cell.el, this.text);
      loadCapacity();
    },
    update(snap) {
      const b = snap.battery;
      if (!this.cell) return;
      this.cell.set(b.percent, b.state === 'charging');
      const healthy = b.health_pct >= 85;
      this.text.replaceChildren(
        el('div', { class: 'battery-state' }, `${b.state[0].toUpperCase()}${b.state.slice(1)}`),
        el('div', { class: 'hint' }, b.time_left_s ? `${fmt.duration(b.time_left_s)} ${b.state === 'charging' ? 'to full' : 'left'}` : b.power_w ? fmt.watts(Math.abs(b.power_w)) : ''),
        b.grade ? el('span', { class: `badge ${healthy ? 'good' : 'bad'}` }, `${healthy ? '✓' : '!'} Health ${b.health_pct.toFixed(1)} % · ${b.grade}`) : null,
        b.cycles != null ? el('div', { class: 'hint' }, `${b.cycles} charge cycles`) : null);
      kv($('#battery-kv'), [
        ['Power', b.power_w ? `${b.power_w > 0 ? 'Charging' : 'Draining'} ${fmt.watts(Math.abs(b.power_w))}` : 'Idle'],
        ['Voltage', b.voltage_v != null ? `${b.voltage_v.toFixed(2)} V` : null],
        ['Energy now', fmt.energy(b.remaining_mwh)], ['Full charge capacity', fmt.energy(b.full_mwh)],
        ['Design capacity', `${fmt.energy(b.design_mwh)}${b.design_source ? ` (${b.design_source})` : ''}`],
        ['Health', b.health_pct != null ? `${b.health_pct.toFixed(1)} %` : null], ['Wear', b.wear_pct != null ? `${b.wear_pct.toFixed(1)} %` : null],
        ['Charge cycles', b.cycles], ['Maker / chemistry', [b.manufacturer, b.chemistry].filter(Boolean).join(' · ') || null],
      ]);
      setChart('batt-pct', state.live);
      setChart('batt-power', state.live);
    },
  },

  storage: {
    update(snap) {
      const m = snap.memory, s = snap.storage;
      $('#memory').replaceChildren(volumeRow('RAM', m.used, m.total), m.swap_total ? volumeRow('Page file', m.swap_used, m.swap_total) : '');
      $('#volumes').replaceChildren(...s.volumes.map((v) => volumeRow(`${v.mount} (${v.fs})`, v.used, v.total)));
      const disks = s.disks || [];
      $('#disks').replaceChildren(
        el('tr', {}, ...['Drive', 'Type', 'Health', 'Temperature', 'Wear', 'Size'].map((h, i) => el('th', { class: i > 2 ? 'num' : '' }, h))),
        ...(disks.length ? disks.map((d) => el('tr', {},
          el('td', { class: 'name' }, d.name), el('td', {}, `${d.bus} ${d.media}`),
          el('td', {}, el('span', { class: `badge ${d.health === 'Healthy' ? 'good' : 'bad'}` }, `${d.health === 'Healthy' ? '✓' : '!'} ${d.health}`)),
          el('td', { class: 'num' }, d.temp_c ? fmt.temp(d.temp_c) : '—'), el('td', { class: 'num' }, d.wear_pct != null ? `${d.wear_pct} %` : '—'),
          el('td', { class: 'num' }, fmt.bytes(d.size, 0))))
          : [el('tr', {}, el('td', { colspan: 6 }, 'Reading drive health…'))]));
      setChart('disk-io', state.live);
    },
  },

  network: {
    update(snap) {
      const n = snap.network;
      kv($('#net-kv'), [['Download', fmt.rate(n.down_bps)], ['Upload', fmt.rate(n.up_bps)],
        ['Received since boot', fmt.bytes(n.total_down)], ['Sent since boot', fmt.bytes(n.total_up)]]);
      setChart('net-io', state.live);
    },
  },

  processes: {
    update(snap) {
      $('#procs').replaceChildren(
        el('tr', {}, el('th', {}, 'Name'), el('th', { class: 'num' }, 'PID'), el('th', { class: 'num' }, 'CPU'), el('th', { class: 'num' }, 'Memory'), el('th', {}, '')),
        ...snap.processes.map((p) => el('tr', {},
          el('td', { class: 'name', title: p.name }, p.name), el('td', { class: 'num' }, p.pid),
          el('td', { class: 'num' }, el('div', { class: 'cell-bar' }, el('span', { style: `width:${Math.min(100, p.cpu_pct)}%` }), `${p.cpu_pct.toFixed(1)} %`)),
          el('td', { class: 'num' }, fmt.bytes(p.mem)),
          el('td', {}, el('button', { class: 'btn danger small', onclick: () => endProcess(p) }, 'End')))));
    },
  },

  ram: { show: () => startRamPolling() },
  internet: { show: () => showInternet() },
  power: { show: () => loadPower() },
  bench: { show: () => loadBenchHistory(), update: () => updateStressChart() },
  history: { show: () => loadHistory() },
  settings: { show: () => renderSettings() },
};

function renderSensorTable(sensors) {
  const table = $('#sensor-table');
  const key = sensors.map((s) => s.hardware + s.name).join('|');
  if (!sensors.length) {
    table.replaceChildren(el('tr', {}, el('td', {}, 'No extra sensors yet. See "Not seeing your fans?" above.')));
    table.dataset.key = '';
    return;
  }
  if (table.dataset.key !== key) {
    table.dataset.key = key;
    const rows = [el('tr', {}, el('th', {}, 'Sensor'), el('th', {}, 'Type'), el('th', { class: 'num' }, 'Value'), el('th', {}, 'Source'))];
    let hardware = null;
    sensors.forEach((s, i) => {
      if (s.hardware !== hardware) {
        hardware = s.hardware;
        rows.push(el('tr', { class: 'group-row' }, el('td', { colspan: 4 }, hardware)));
      }
      rows.push(el('tr', {}, el('td', {}, s.name), el('td', {}, s.type), el('td', { class: 'num', 'data-i': i }), el('td', { class: 'hint' }, s.source)));
    });
    table.replaceChildren(...rows);
  }
  for (const cell of $$('td[data-i]', table)) {
    const s = sensors[Number(cell.dataset.i)];
    const value = s.type === 'Temperature' && s.value != null ? fmt.temp(s.value, 1)
      : s.value == null ? '—' : `${Number.isInteger(s.value) ? s.value : s.value.toFixed(2)} ${s.unit}`;
    if (cell.textContent !== value) cell.textContent = value;
  }
}

function volumeRow(label, used, total) {
  const pct = total ? (used / total) * 100 : 0;
  return el('div', { class: 'volume' },
    el('div', { class: 'volume-head' }, el('span', {}, label), el('span', { class: 'hint' }, `${fmt.bytes(used)} of ${fmt.bytes(total)} (${pct.toFixed(0)} %)`)),
    el('div', { class: `bar${pct > 90 ? ' warn' : ''}` }, el('span', { style: `width:${pct}%` })));
}

async function endProcess(p) {
  if (!confirm(`End "${p.name}" (PID ${p.pid})? Unsaved work in it will be lost.`)) return;
  try {
    const r = await api(`/api/processes/${p.pid}/end`, { method: 'POST' });
    toast(`Ended ${r.name}`);
  } catch (err) { toast(err.message); }
}

async function loadCapacity() {
  try {
    const rows = await api('/api/battery/capacity');
    // Noon local time, so a date never shifts by the UTC offset.
    setChart('batt-capacity', rows.map((r) => ({ ts: new Date(`${r.day}T12:00`).getTime() / 1000, full_wh: r.full_mwh / 1000, design_wh: r.design_mwh ? r.design_mwh / 1000 : null })));
  } catch (err) { toast(err.message); }
}

// ------------------------------------------------------------------ RAM map

const ram = { timer: null, built: false };
const RAM_PARTS = [
  ['process_private', 'Process private'], ['shared_other', 'Shared & other'], ['nonpaged_pool', 'Non-paged pool'],
  ['paged_pool', 'Paged pool'], ['file_cache', 'File cache'], ['drivers_code', 'Drivers & kernel code'],
];

function startRamPolling() {
  if (!ram.built) {
    ram.lists = new UI.StackBar();
    ram.active = new UI.StackBar();
    ram.priority = new UI.Columns(['0', '1', '2', '3', '4', '5', '6', '7'], '--series-3');
    $('#ram-lists').replaceChildren(ram.lists.el);
    $('#ram-active').replaceChildren(ram.active.el);
    $('#ram-priority').replaceChildren(ram.priority.el);
    ram.built = true;
  }
  clearInterval(ram.timer);
  const tick = () => { if (state.view === 'ram') loadRam(); else clearInterval(ram.timer); };
  tick();
  ram.timer = setInterval(tick, 2000);
}

async function loadRam() {
  let data;
  try { data = await api('/api/memory/map'); } catch (err) { toast(err.message); return; }
  const m = data.map;
  ram.lists.set([
    { key: 'active', label: 'In use', value: m.active, color: '--series-1', text: fmt.bytes(m.active) },
    { key: 'modified', label: 'Modified', value: m.modified, color: '--series-2', text: fmt.bytes(m.modified) },
    { key: 'standby', label: 'Standby (cache)', value: m.standby, color: '--series-3', text: fmt.bytes(m.standby) },
    { key: 'free', label: 'Free', value: m.free, color: '--series-4', text: fmt.bytes(m.free) },
    { key: 'zeroed', label: 'Zeroed', value: m.zeroed, color: '--series-5', text: fmt.bytes(m.zeroed) },
  ], m.total);
  ram.active.set(RAM_PARTS.map(([key, label], i) => ({
    key, label, value: m.active_parts[key], color: `--series-${i + 1}`, text: fmt.bytes(m.active_parts[key]),
  })), m.active);
  if (m.standby_by_priority) {
    ram.priority.set(m.standby_by_priority, (v) => fmt.bytes(v, 0),
      m.standby_by_priority.map((v, i) => `Priority ${i}: ${fmt.bytes(v)} (${(m.repurposed_by_priority?.[i] || 0).toLocaleString()} pages repurposed since boot)`));
  } else {
    $('#ram-priority').replaceChildren(el('p', { class: 'hint' }, 'Windows did not report standby priorities.'));
  }
  kv($('#ram-kv'), [
    ['Total', fmt.bytes(m.total)], ['Available', fmt.bytes(m.available)],
    ['Committed', `${fmt.bytes(m.committed)} of ${fmt.bytes(m.commit_limit)}`],
    ['Paged pool (total)', fmt.bytes(m.paged_pool_total)],
    ['Page faults', m.page_faults_per_s != null ? `${Math.round(m.page_faults_per_s).toLocaleString()} /s` : null],
    ['Hard faults (from disk)', m.hard_faults_per_s != null ? `${Math.round(m.hard_faults_per_s).toLocaleString()} /s` : null],
  ]);
  if (!ram.commandsBuilt) {
    $('#ram-commands').replaceChildren(...Object.entries(data.commands).map(([key, c]) => el('div', { class: 'command' },
      el('b', {}, c.label), el('div', { class: 'hint' }, c.help),
      el('button', { class: 'btn', disabled: !data.admin, onclick: () => runRamCommand(key, c.label) }, data.admin ? 'Run' : 'Needs admin'))));
    if (!data.admin) $('#ram-admin-note').textContent += ' Start OMNI as administrator to use them.';
    ram.commandsBuilt = true;
  }
  const shown = ram.showAll ? data.processes : data.processes.slice(0, 10);
  const biggest = data.processes[0]?.working_set || 1;
  $('#ram-procs').replaceChildren(
    el('tr', {}, el('th', {}, 'Process'), el('th', { class: 'num' }, 'PID'), el('th', { class: 'num' }, 'Working set'), el('th', { class: 'num' }, 'Private'), el('th', {}, '')),
    ...shown.map((p) => el('tr', {}, el('td', { class: 'name', title: p.name }, p.name), el('td', { class: 'num' }, p.pid),
      el('td', { class: 'num' }, el('div', { class: 'cell-bar' }, el('span', { style: `width:${(p.working_set / biggest) * 100}%` }), fmt.bytes(p.working_set))),
      el('td', { class: 'num' }, fmt.bytes(p.private)),
      el('td', {}, el('button', { class: 'btn small', onclick: () => trimProcess(p) }, 'Trim')))),
    data.processes.length > 10 ? el('tr', {}, el('td', { colspan: 5, class: 'more-row' },
      el('button', { class: 'btn small', onclick: () => { ram.showAll = !ram.showAll; loadRam(); } },
        ram.showAll ? 'Show fewer' : `Show all ${data.processes.length}`))) : '');
}

async function runRamCommand(key, label) {
  if (!confirm(`${label}? Programs may feel slower for a moment while Windows reloads data.`)) return;
  const out = $('#ram-command-result');
  out.textContent = `${label}…`;
  try {
    const r = await api('/api/memory/command', { method: 'POST', body: { command: key } });
    const row = (name, k) => el('div', {}, el('dt', {}, name), el('dd', {}, `${fmt.bytes(r.before[k])} → ${fmt.bytes(r.after[k])}`));
    out.replaceChildren(el('p', {}, el('b', {}, `${r.label}: done in ${r.seconds} s`)),
      el('div', { class: 'kv reveal' }, row('Standby', 'standby'), row('Modified', 'modified'), row('Free', 'free')));
    loadRam();
  } catch (err) { out.textContent = err.message; }
}

async function trimProcess(p) {
  try {
    const r = await api(`/api/memory/trim/${p.pid}`, { method: 'POST' });
    toast(`${r.name}: ${fmt.bytes(r.before)} → ${fmt.bytes(r.after)}`);
    loadRam();
  } catch (err) { toast(err.message); }
}

// ------------------------------------------------------------------ internet test

const net = { gauge: null, job: null };

function showInternet() {
  if (!net.gauge) {
    // Log scale: 1 Mbps and 1 Gbps both have room on the dial.
    net.gauge = new UI.Gauge({ label: 'Speed', unit: 'Mbps', max: 1000, format: (v) => (v == null ? '—' : v < 10 ? v.toFixed(1) : Math.round(v).toString()),
      scale: (v) => (Math.log10(1 + v) / Math.log10(1001)) * 100 });
    net.gauge.el.classList.add('speed-gauge');
    $('#speed-gauge').replaceChildren(net.gauge.el);
    net.gauge.set(null, 'Ready');
  }
  api('/api/net/wifi').then((w) => kv($('#wifi-kv'), w ? [
    ['Network', w.ssid], ['Signal', w.signal_pct != null ? `${w.signal_pct} %${w.rssi_dbm != null ? ` (${w.rssi_dbm} dBm)` : ''}` : null],
    ['Band / channel', [w.band, w.channel && `ch ${w.channel}`].filter(Boolean).join(' · ')], ['Standard', w.radio],
    ['Link speed', w.receive_mbps ? `${w.receive_mbps} ↓ / ${w.transmit_mbps} ↑ Mbps` : null], ['Security', w.auth],
  ] : [['Wi-Fi', 'Not connected over Wi-Fi (or no Wi-Fi adapter)']])).catch(() => {});
  loadSpeedHistory();
}

async function loadSpeedHistory() {
  try {
    const rows = (await api('/api/bench/results')).filter((r) => r.kind === 'speedtest').reverse();
    setChart('speed-history', rows.map((r) => ({ ts: r.ts, download_mbps: r.result.download_mbps ?? null, upload_mbps: r.result.upload_mbps ?? null })));
  } catch { /* history is optional */ }
}

function renderSpeedResult(r, partial = false) {
  const cell = (label, value, unitText, extra) => el('div', { class: `result${partial ? '' : ' reveal'}` },
    el('div', { class: 'result-label' }, label), el('div', { class: 'result-value' }, value ?? '—', value != null ? el('span', {}, ` ${unitText}`) : ''), extra || '');
  const grade = r.bufferbloat_grade;
  $('#speed-result').replaceChildren(
    cell('Download', r.download_mbps, 'Mbps'), cell('Upload', r.upload_mbps, 'Mbps'),
    cell('Latency', r.latency_ms, 'ms', r.jitter_ms != null ? el('div', { class: 'hint' }, `Jitter ${r.jitter_ms} ms`) : ''),
    cell('Under load', r.loaded_latency_ms, 'ms', grade ? el('span', { class: `badge ${'AB'.includes(grade[0]) ? 'good' : 'bad'}` }, `Bufferbloat ${grade}`) : ''),
    cell('Server', r.download_server || null, '', r.upload_server && r.upload_server !== r.download_server
      ? el('div', { class: 'hint' }, `Upload: ${r.upload_server}`) : ''),
    cell('Data used', r.download_bytes || r.upload_bytes ? fmt.bytes((r.download_bytes || 0) + (r.upload_bytes || 0)) : null, ''),
    ...(r.notes || []).map((note) => el('div', { class: 'result-note reveal' }, note)));
}

async function startSpeedTest() {
  const button = $('#speed-start');
  if (net.job && net.job.status === 'running') {
    api(`/api/jobs/${net.job.id}/stop`, { method: 'POST' });
    return;
  }
  const parts = $$('#view-internet .check input:checked').map((i) => i.value);
  if (!parts.length) { toast('Pick at least one part of the test'); return; }
  button.textContent = 'Stop';
  const phases = $$('#speed-phases span');
  try {
    const job = await runJob('/api/net/speedtest', { parts }, (j) => {
      if (/Connecting|Finding/.test(j.message)) net.gauge.set(null, j.message);
      net.job = j;
      const phase = /latency/i.test(j.message) ? 'latency' : /download/i.test(j.message) ? 'download' : /upload/i.test(j.message) ? 'upload' : null;
      phases.forEach((p) => p.classList.toggle('active', p.dataset.phase === phase));
      $('#speed-status').textContent = j.message;
      if (phase === 'latency') {
        const ms = Number((j.message.match(/([\d.]+) ms/) || [])[1]);
        net.gauge.set(null, Number.isFinite(ms) ? `Latency ${ms} ms` : 'Measuring latency');
      } else if (j.live != null) net.gauge.set(j.live, phase === 'upload' ? 'Upload' : 'Download');
    }, 300, 'speedtest');
    phases.forEach((p) => p.classList.remove('active'));
    if (job.status === 'failed') { $('#speed-status').textContent = `Failed: ${job.error}`; net.gauge.set(null, 'Failed'); }
    else if (job.result) {
      const r = job.result;
      renderSpeedResult(r);
      net.gauge.set(r.download_mbps ?? r.upload_mbps ?? null, r.download_mbps != null ? 'Download' : r.upload_mbps != null ? 'Upload' : `Latency ${r.latency_ms} ms`);
      $('#speed-status').textContent = job.status === 'stopped' ? 'Stopped' : 'Done';
      loadSpeedHistory();
    }
  } catch (err) { $('#speed-status').textContent = err.message; }
  button.textContent = 'Start test';
}

async function runDiagnostics() {
  const button = $('#diag-start');
  button.disabled = true;
  try {
    const job = await runJob('/api/net/diagnostics', undefined, (j) => { $('#diag-status').textContent = j.message; });
    if (job.status === 'failed') { $('#diag-status').textContent = `Failed: ${job.error}`; return; }
    const r = job.result;
    $('#ping-table').replaceChildren(
      el('tr', {}, el('th', {}, 'Network'), el('th', { class: 'num' }, 'Latency'), el('th', { class: 'num' }, 'Jitter'), el('th', { class: 'num' }, 'Loss')),
      ...r.ping.map((p) => el('tr', { class: 'reveal' }, el('td', {}, `${p.name} (${p.host})`), el('td', { class: 'num' }, p.latency_ms != null ? `${p.latency_ms} ms` : '—'),
        el('td', { class: 'num' }, p.jitter_ms != null ? `${p.jitter_ms} ms` : '—'),
        el('td', { class: 'num' }, el('span', { class: `badge ${p.loss_pct ? 'bad' : 'good'}` }, `${p.loss_pct ? '!' : '✓'} ${p.loss_pct} %`)))));
    const times = r.dns.filter((d) => d.median_ms != null).map((d) => d.median_ms);
    const best = Math.min(...times), worst = Math.max(...times, 1);
    $('#dns-bars').replaceChildren(...r.dns.map((d) => el('div', { class: 'dns-row reveal' },
      el('div', { class: 'dns-name' }, el('b', {}, d.resolver), ` ${d.address || ''}`, d.current ? el('span', { class: 'badge' }, 'current') : '',
        d.median_ms === best ? el('span', { class: 'badge good' }, '✓ fastest') : ''),
      el('div', { class: 'bar' }, el('span', { style: `width:${d.median_ms ? (d.median_ms / worst) * 100 : 0}%` })),
      el('div', { class: 'dns-ms' }, d.failed ? 'No answer' : `${d.median_ms} ms`))),
      el('p', { class: 'hint' }, 'Median of 6 lookups each. Lower is better; results vary from run to run.'));
    $('#diag-status').textContent = 'Done';
  } catch (err) { $('#diag-status').textContent = err.message; }
  button.disabled = false;
}

// ------------------------------------------------------------------ power plans

const BOOST_LABELS = { 0: 'Off', 1: 'On', 2: 'Aggressive', 3: 'Efficient', 4: 'Efficient aggressive', 5: 'Aggressive at guaranteed', 6: 'Efficient aggressive at guaranteed' };

async function loadPower() {
  let data;
  try { data = await api('/api/power'); } catch (err) { toast(err.message); return; }
  $('#plans').replaceChildren(...data.plans.map((p) => el('label', { class: `choice${p.active ? ' active' : ''}` },
    el('input', { type: 'radio', name: 'plan', checked: p.active, onchange: () => setPlan(p) }), p.name)));
  $$('#power-mode button').forEach((b) => b.classList.toggle('active', b.dataset.mode === data.mode));

  const boost = data.tweaks.boost_on_battery, cap = data.tweaks.max_cpu_on_battery;
  const boostSelect = el('select', {}, ...Object.entries(BOOST_LABELS).map(([v, label]) =>
    el('option', { value: v, selected: Number(v) === boost.dc }, label)));
  const capInput = el('input', { type: 'number', min: 5, max: 100, value: cap.dc ?? 100 });
  $('#tweaks').replaceChildren(
    el('div', { class: 'tweak' }, el('b', {}, `${boost.label}${boost.changed ? ' (changed by OMNI)' : ''}`),
      el('div', { class: 'toolbar' }, boostSelect, el('button', { class: 'btn', onclick: () => applyTweak('boost_on_battery', boostSelect.value) }, 'Apply')),
      el('div', { class: 'help' }, `${boost.help} Plugged in: ${BOOST_LABELS[boost.ac] ?? '—'}.`)),
    el('div', { class: 'tweak' }, el('b', {}, `${cap.label}${cap.changed ? ' (changed by OMNI)' : ''}`),
      el('div', { class: 'toolbar' }, capInput, '%', el('button', { class: 'btn', onclick: () => applyTweak('max_cpu_on_battery', capInput.value) }, 'Apply')),
      el('div', { class: 'help' }, `${cap.help} Plugged in: ${cap.ac ?? '—'} %.`)));
  $('#tweak-restore').disabled = !data.can_restore;
}

async function setPlan(plan) {
  try { await api('/api/power/plan', { method: 'POST', body: { guid: plan.guid } }); toast(`Power plan: ${plan.name}`); }
  catch (err) { toast(err.message); }
  loadPower();
}

async function applyTweak(name, value) {
  try { await api('/api/power/tweak', { method: 'POST', body: { name, dc_value: Number(value) } }); toast('Applied'); }
  catch (err) { toast(err.message); }
  loadPower();
}

// ------------------------------------------------------------------ jobs (benchmarks, stress, cleanup)

async function runJob(startPath, body, onUpdate, pollMs = 700, kind = null) {
  let job;
  try {
    job = await api(startPath, { method: 'POST', body });
  } catch (err) {
    // Same kind of job already running (e.g. started in another tab, or before a reload):
    // follow it instead of failing with "already running".
    const current = err.status === 409 ? await api('/api/jobs/current') : null;
    if (!current || (kind && current.kind !== kind)) throw err;
    job = current;
  }
  onUpdate(job);
  while (job.status === 'running') {
    await new Promise((r) => setTimeout(r, pollMs));
    job = await api(`/api/jobs/${job.id}`);
    onUpdate(job);
  }
  return job;
}

const BENCHES = {
  cpu: ['CPU', '⚙', 'Single-core speed, then every thread at once in separate processes.', (r) => [
    ['Single-core', `${r.single_score.toLocaleString()} pts`], ['Multi-core', `${r.multi_score.toLocaleString()} pts`],
    ['Scaling', `${r.scaling}× on ${r.threads} threads`]]],
  memory: ['Memory', '▤', 'Native memcpy / memset bandwidth on a 512 MiB buffer.', (r) => [
    ['Copy', fmt.rate(r.copy_bps)], ['Write', fmt.rate(r.write_bps)]]],
  disk: ['SSD', '⛁', 'Unbuffered 1 GiB write and read, then random 4 KiB reads (QD1). Uses your temp folder.', (r) => [
    ['Sequential read', fmt.rate(r.seq_read_bps)], ['Sequential write', fmt.rate(r.seq_write_bps)],
    ['Random 4K', `${r.rand4k_iops.toLocaleString()} IOPS`], ['4K latency', `${r.rand4k_latency_ms_avg} ms avg · ${r.rand4k_latency_ms_p99} ms p99`]]],
  gpu: ['GPU', '◧', 'FP32 fused multiply-add throughput through CUDA (NVIDIA).', (r) => [
    ['FP32', `${r.fp32_tflops} TFLOPS`], ['Device', r.device]]],
};

function renderBenchCards() {
  $('#bench-cards').replaceChildren(...Object.entries(BENCHES).map(([kind, [title, icon, desc]]) => {
    const progress = el('div', { class: 'progress', hidden: true }, el('span'));
    const status = el('div', { class: 'hint' });
    const result = el('div', { class: 'bench-result' });
    const stop = el('button', { class: 'btn', hidden: true }, 'Stop');
    const run = el('button', { class: 'btn primary' }, 'Run');
    const card = el('div', { class: 'card bench-card' }, el('div', { class: 'bench-icon', 'aria-hidden': 'true' }, icon),
      el('h2', {}, title), el('p', { class: 'hint' }, desc), result, progress, status, el('div', { class: 'toolbar' }, run, stop));
    run.addEventListener('click', async () => {
      run.disabled = true; progress.hidden = false; result.replaceChildren(); card.classList.add('running');
      let jobId = null;
      stop.hidden = false;
      stop.onclick = () => jobId && api(`/api/jobs/${jobId}/stop`, { method: 'POST' });
      try {
        const job = await runJob(`/api/bench/${kind}`, undefined, (j) => {
          jobId = j.id;
          progress.firstChild.style.width = `${j.progress * 100}%`;
          status.textContent = j.message;
        });
        if (job.status === 'failed') status.textContent = `Failed: ${job.error}`;
        else if (job.result) {
          const rows = BENCHES[kind][3](job.result);
          result.replaceChildren(...rows.map(([k, v], i) => el('div', { class: 'reveal' }, i === 0 ? el('div', { class: 'big' }, v) : el('b', {}, v), ' ', el('span', { class: 'hint' }, k))));
          status.textContent = 'Done';
          loadBenchHistory();
        }
      } catch (err) { status.textContent = err.message; }
      run.disabled = false; stop.hidden = true; progress.hidden = true; card.classList.remove('running');
    });
    return card;
  }));
}

async function loadBenchHistory() {
  let rows;
  try { rows = await api('/api/bench/results'); } catch { return; }
  const summary = (kind, r) => {
    const def = BENCHES[kind.replace('bench-', '')];
    return def ? def[3](r).map(([k, v]) => `${k}: ${v}`).join(' · ') : '';
  };
  $('#bench-history').replaceChildren(
    el('tr', {}, el('th', {}, 'When'), el('th', {}, 'Test'), el('th', {}, 'Result')),
    ...(rows.length ? rows.map((row) => el('tr', {}, el('td', {}, fmt.time(row.ts)), el('td', {}, row.kind.replace('bench-', '')),
      el('td', {}, summary(row.kind, row.result))))
      : [el('tr', {}, el('td', { colspan: 3 }, 'No results yet.'))]));
}

let stressJob = null;
function updateStressChart() {
  if (!stressJob) return;
  $('[data-chart="stress"]').hidden = false;
  if (stressJob.status === 'running') {
    setChart('stress', state.live.filter((p) => p.ts >= stressJob.started));
  } else if (stressJob.result) {
    setChart('stress', stressJob.result.samples.map((s) => ({ ts: stressJob.started + s.t, cpu_temp_c: s.cpu_temp_c, gpu_temp_c: s.gpu_temp_c })));
  }
}

async function startStress() {
  const button = $('#stress-start');
  if (stressJob && stressJob.status === 'running') {
    api(`/api/jobs/${stressJob.id}/stop`, { method: 'POST' });
    return;
  }
  const seconds = Number($('#stress-seconds').value);
  const out = $('#stress-result');
  button.textContent = 'Stop';
  try {
    const job = await runJob('/api/stress', { target: state.stressTarget, seconds }, (j) => {
      stressJob = j;
      out.replaceChildren(el('div', { class: 'progress' }, el('span', { style: `width:${j.progress * 100}%` })), el('p', { class: 'hint' }, j.message));
      updateStressChart();
    });
    if (job.status === 'failed') out.replaceChildren(el('p', {}, `Failed: ${job.error}`));
    else if (job.result) {
      const r = job.result;
      out.replaceChildren(
        el('div', { class: 'kv' }, ...[
          ['CPU peak', fmt.temp(r.cpu_temp_peak_c)], ['GPU peak', fmt.temp(r.gpu_temp_peak_c)], ['GPU peak power', fmt.watts(r.gpu_power_peak_w)],
          r.cpu_clock_early_mhz ? ['CPU clock start → end', `${fmt.freq(r.cpu_clock_early_mhz)} → ${fmt.freq(r.cpu_clock_late_mhz)}`] : null,
        ].filter(Boolean).map(([k, v]) => el('div', {}, el('dt', {}, k), el('dd', {}, v)))),
        el('ul', {}, ...r.findings.map((f) => el('li', {}, f))));
      updateStressChart();
    }
  } catch (err) { out.replaceChildren(el('p', {}, err.message)); }
  button.textContent = 'Start';
}

// ------------------------------------------------------------------ cleanup

async function scanCleanup() {
  const button = $('#cleanup-scan');
  button.disabled = true; button.textContent = 'Scanning…';
  $('#cleanup-result').replaceChildren();
  try {
    const rows = await api('/api/cleanup/scan');
    $('#cleanup-table').replaceChildren(
      el('tr', {}, el('th', {}, ''), el('th', {}, 'What'), el('th', { class: 'num' }, 'Files'), el('th', { class: 'num' }, 'Size')),
      ...rows.map((r) => el('tr', {},
        el('td', {}, el('input', { type: 'checkbox', value: r.key, checked: r.allowed && r.files > 0, disabled: !r.allowed || !r.files })),
        el('td', {}, el('div', {}, r.label), el('div', { class: 'hint' }, r.allowed ? r.note || r.path : 'Needs OMNI to run as administrator')),
        el('td', { class: 'num' }, r.files.toLocaleString()), el('td', { class: 'num' }, fmt.bytes(r.bytes)))));
    $('#cleanup-run').hidden = !rows.some((r) => r.allowed && r.files);
  } catch (err) { toast(err.message); }
  button.disabled = false; button.textContent = 'Scan again';
}

async function runCleanup() {
  const keys = $$('#cleanup-table input:checked').map((i) => i.value);
  if (!keys.length) { toast('Nothing selected'); return; }
  if (!confirm('Delete the selected files? This cannot be undone.')) return;
  const result = $('#cleanup-result');
  try {
    const job = await runJob('/api/cleanup/run', { categories: keys }, (j) => { result.textContent = j.message; });
    if (job.status === 'failed') { result.textContent = `Failed: ${job.error}`; return; }
    const r = job.result;
    result.replaceChildren(el('p', {}, el('b', {}, `Freed ${fmt.bytes(r.deleted_bytes)}`), ` from ${r.deleted_files.toLocaleString()} files.`,
      r.skipped_files ? ` ${r.skipped_files.toLocaleString()} files were in use and were left alone.` : ''));
    $('#cleanup-run').hidden = true;
  } catch (err) { result.textContent = err.message; }
}

// ------------------------------------------------------------------ history

async function loadHistory() {
  const metrics = 'cpu_pct,gpu_pct,ram_pct,cpu_temp_c,gpu_temp_c,batt_pct,gpu_w,cpu_w,system_w,fan_rpm';
  try {
    const data = await api(`/api/history?metrics=${metrics}&range=${state.historyRange}`);
    const points = data.ts.map((ts, i) => {
      const p = { ts };
      for (const key of metrics.split(',')) p[key] = data[key][i];
      return p;
    });
    ['h-usage', 'h-temps', 'h-power', 'h-fans', 'h-batt', 'h-gpu-power'].forEach((name) => setChart(name, points));
  } catch (err) { toast(err.message); }
}

async function exportHistory(event) {
  event.preventDefault();
  const range = ['24h', '7d', '30d'].includes(state.historyRange) ? state.historyRange : '24h';
  try {
    const res = await fetch(`/api/history/export?range=${range}`, { headers: { 'X-OMNI-Token': TOKEN } });
    if (!res.ok) throw new Error('Export failed');
    const url = URL.createObjectURL(await res.blob());
    el('a', { href: url, download: `omni-history-${range}.csv` }).click();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
  } catch (err) { toast(err.message); }
}

// ------------------------------------------------------------------ settings

const SETTING_TEXT = {
  general: ['General', {
    refresh_interval_s: ['Refresh interval (s)', 'How often live readings update.'],
    theme: ['Theme', 'System follows Windows.'],
    web_port: ['Dashboard port', 'Takes effect next time OMNI starts.'],
    open_browser: ['Open browser on start', ''],
  }],
  display: ['Display', {
    animations: ['Animations', 'Smooth gauges, spinning fans and transitions. Also off when Windows "Animation effects" is off.'],
    fan_max_rpm: ['Fan full-scale (RPM)', 'The RPM shown as 100 % on fan gauges.'],
  }],
  units: ['Units', {
    temperature: ['Temperature', 'C = Celsius (metric), F = Fahrenheit.'],
    data: ['Data sizes', 'si = metric (1 GB = 1000 MB), iec = binary (1 GiB = 1024 MiB).'],
    energy: ['Battery energy', 'Wh or mWh.'],
  }],
  sensors: ['Sensors & fans', {
    interval_s: ['Fan / sensor refresh (s)', ''],
    hp_bios: ['HP OMEN / Victus BIOS', 'Fan speeds and max-fan switch on HP gaming laptops (needs administrator).'],
    vendor_wmi: ['Maker sensors (Dell, HP business, Lenovo)', 'Fan speeds from Dell Command | Monitor, HP EliteBook/ProBook/ZBook BIOS sensors, Lenovo Legion/LOQ (experimental) and Win32_Fan.'],
    hwmon_bridge: ['LibreHardwareMonitor bridge', 'Reads every sensor while LibreHardwareMonitor or OpenHardwareMonitor runs.'],
    hwinfo_bridge: ['HWiNFO bridge', 'Reads every sensor while HWiNFO runs with Shared Memory Support on.'],
  }],
  network: ['Internet test', {
    speedtest_server: ['Speed-test server', 'auto = Cloudflare, or the nearest mirror if Cloudflare is busy. A custom address must offer Cloudflare-style /__down and /__up.'],
    streams: ['Parallel streams', 'More streams fill fast lines; 4 suits most connections.'],
    seconds: ['Seconds per direction', 'Longer tests are steadier but use more data.'],
  }],
  gpu: ['GPU', {
    enabled: ['Read NVIDIA driver details', ''],
    poll_on_battery: ['Poll NVIDIA GPU on battery', 'Off lets an Optimus GPU sleep on battery and saves power.'],
  }],
  history: ['History', {
    enabled: ['Record history', 'Stored locally in SQLite.'],
    sample_every_s: ['Save a sample every (s)', ''],
    retention_days: ['Keep history for (days)', ''],
  }],
  alerts: ['Alerts', {
    enabled: ['Show alerts', ''],
    battery_low_pct: ['Low battery at (%)', '0 turns it off.'],
    charge_limit_pct: ['Charge reminder at (%)', 'Reminds you to unplug while charging past this. 0 or 100 turns it off.'],
    cpu_temp_c: ['CPU too hot at (°C)', ''],
    gpu_temp_c: ['GPU too hot at (°C)', ''],
    ssd_temp_c: ['SSD too hot at (°C)', ''],
  }],
  battery: ['Battery', {
    design_capacity_override_mwh: ['Design capacity override (mWh)', '0 = read it from Windows automatically.'],
  }],
  cleanup: ['Cleanup', {
    min_age_hours: ['Only delete files older than (hours)', 'Protects files apps are still using.'],
  }],
  processes: ['Processes', {
    protected: ['Protected processes', 'Comma-separated names OMNI will never end.'],
  }],
};

async function loadSettings() {
  const data = await api('/api/settings');
  state.settings = data.values;
  state.schema = data.schema;
  applyTheme();
}

function renderSettings() {
  if (!state.settings) return;
  const order = Object.keys(SETTING_TEXT).filter((g) => state.schema[g]);
  $('#settings-form').replaceChildren(...order.map((group) => {
    const keys = state.schema[group];
    const [groupLabel, labels] = SETTING_TEXT[group];
    return el('fieldset', {}, el('legend', {}, groupLabel), ...Object.entries(keys).map(([key, spec]) => {
      const [label, desc] = labels[key] || [key, ''];
      const value = state.settings[group][key];
      const id = `s-${group}-${key}`;
      let input;
      if (typeof spec.default === 'boolean') {
        input = el('input', { type: 'checkbox', id, checked: value });
        input.dataset.group = group; input.dataset.key = key;
        input = el('label', { class: 'switch' }, input, el('span', { class: 'slider' }));
      } else {
        if (Array.isArray(spec.allowed) && typeof spec.default === 'string') {
          input = el('select', { id }, ...spec.allowed.map((opt) => el('option', { value: opt, selected: opt === value }, opt)));
        } else if (typeof spec.default === 'number') {
          input = el('input', { type: 'number', id, value, min: spec.allowed[0], max: spec.allowed[1], step: Number.isInteger(spec.default) ? 1 : 0.5 });
        } else input = el('input', { type: 'text', id, value });
        input.dataset.group = group; input.dataset.key = key;
      }
      return el('div', { class: 'setting' }, el('label', { for: id }, el('div', {}, label), desc ? el('div', { class: 'desc' }, desc) : null), input);
    }));
  }));
  api('/api/info').then((info) => kv($('#about'), [
    ['Version', info.version], ['Running as administrator', info.admin ? 'Yes' : 'No (fans and some cleanup need it)'],
    ['GPU', info.gpu || info.gpu_error], ['Sensor sources', (info.sensor_sources || []).join(', ') || 'Windows only'],
    ['Data folder', info.data_dir],
  ])).catch(() => {});
}

async function saveSettings(event) {
  event?.preventDefault();
  const changes = {};
  for (const input of $$('#settings-form [data-group]')) {
    const { group, key } = input.dataset;
    const value = input.type === 'checkbox' ? input.checked : input.type === 'number' ? Number(input.value) : input.value;
    if (value !== state.settings[group][key]) (changes[group] ||= {})[key] = value;
  }
  if (!Object.keys(changes).length) { $('#settings-status').textContent = 'No changes.'; return; }
  try {
    state.settings = (await api('/api/settings', { method: 'PUT', body: changes })).values;
    $('#settings-status').textContent = 'Saved.';
    applyTheme();
    state.built = {}; // units or scales may have changed: rebuild views
    render();
  } catch (err) { $('#settings-status').textContent = err.message; }
}

function applyTheme() {
  const theme = state.settings?.general.theme || 'system';
  if (theme === 'system') delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = theme;
  const button = $('#theme-btn');
  button.replaceChildren(Icons.svg({ system: 'monitor', dark: 'moon', light: 'sun' }[theme], 18));
  button.title = `Theme: ${theme} (click to change)`;
  try { localStorage.setItem('omni-theme', theme); } catch { /* ignore */ }
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  document.documentElement.classList.toggle('no-anim', reduce || state.settings?.display?.animations === false);
  Object.values(state.charts).forEach((c) => c.draw());
}

async function cycleTheme() {
  const order = ['system', 'dark', 'light'];
  const next = order[(order.indexOf(state.settings?.general.theme || 'system') + 1) % order.length];
  try {
    state.settings = (await api('/api/settings', { method: 'PUT', body: { general: { theme: next } } })).values;
    applyTheme();
    toast(`Theme: ${next}`);
    if (state.view === 'settings') renderSettings();
  } catch (err) { toast(err.message); }
}

// ------------------------------------------------------------------ wiring

function segmented(selector, attr, onPick) {
  $$(`${selector} button`).forEach((b) => b.addEventListener('click', () => {
    $$(`${selector} button`).forEach((x) => x.classList.toggle('active', x === b));
    onPick(b.dataset[attr]);
  }));
}

function decoratePages() {
  // Each page title gets its section's icon in a tinted badge.
  for (const view of $$('.view')) {
    const name = view.id.replace('view-', '');
    const icon = $(`.sidebar button[data-view="${name}"] .nav-icon`)?.dataset.icon;
    const head = $('.page-head', view);
    if (!head || !icon || $('.page-icon', head)) continue;
    const title = el('div', { class: 'page-title' }, ...head.childNodes);
    head.replaceChildren(el('div', { class: 'page-icon', 'data-icon': icon, 'data-size': 22 }), title);
  }
  Icons.hydrate();
}

// ------------------------------------------------------------------ keyboard

const SHORTCUT_VIEWS = () => $$('.sidebar button[data-view]').map((b) => b.dataset.view);

async function copyReport() {
  try {
    const res = await fetch('/api/report', { headers: { 'X-OMNI-Token': TOKEN } });
    if (!res.ok) throw new Error('Could not build the report');
    await navigator.clipboard.writeText(await res.text());
    toast('Health report copied');
  } catch (err) { toast(err.message || 'Copy failed'); }
}

function openMini() {
  window.open(`${location.pathname}#mini`, 'omni-mini', 'popup,width=460,height=640');
}

function onKey(e) {
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.target.closest('input, textarea, select, [contenteditable]')) return;
  const dialog = $('#help-dialog');
  if (dialog.open) return;
  const views = SHORTCUT_VIEWS();
  if (/^[0-9]$/.test(e.key)) {
    const view = views[e.key === '0' ? 9 : Number(e.key) - 1];
    if (view) showView(view);
  } else if (e.key === '[' || e.key === ']') {
    const next = views.indexOf(state.view) + (e.key === ']' ? 1 : -1);
    showView(views[(next + views.length) % views.length]);
  } else if (e.key === '?') {
    dialog.showModal();
  } else if (e.key === 't' || e.key === 'T') {
    cycleTheme();
  } else if (e.key === 'm' || e.key === 'M') {
    openMini();
  }
}

async function init() {
  decoratePages();
  const windowButtons = $$('#window-select button');
  const markWindow = () => windowButtons.forEach((b) => b.classList.toggle('active', Number(b.dataset.window) === Charts.getWindow()));
  windowButtons.forEach((b) => b.addEventListener('click', () => { Charts.setWindow(Number(b.dataset.window)); markWindow(); }));
  markWindow();
  $$('.sidebar button').forEach((b) => b.addEventListener('click', () => showView(b.dataset.view)));
  window.addEventListener('hashchange', () => showView(location.hash.slice(1) || 'overview'));
  $('#menu-btn').addEventListener('click', () => $('#sidebar').classList.toggle('open'));
  $('#theme-btn').addEventListener('click', cycleTheme);
  $('#mini-btn').addEventListener('click', openMini);
  $('#report-copy').addEventListener('click', copyReport);
  $('#help-btn').addEventListener('click', () => $('#help-dialog').showModal());
  document.addEventListener('keydown', onKey);
  $$('.sidebar button[data-view]').forEach((b, i) => { if (i < 10) b.setAttribute('aria-keyshortcuts', String((i + 1) % 10)); });
  segmented('#proc-sort', 'sort', (sort) => api('/api/processes/sort', { method: 'POST', body: { sort } }).catch((e) => toast(e.message)));
  segmented('#power-mode', 'mode', async (mode) => {
    try { await api('/api/power/mode', { method: 'POST', body: { mode } }); toast('Power mode changed'); }
    catch (err) { toast(err.message); }
    loadPower();
  });
  segmented('#stress-target', 'target', (target) => { state.stressTarget = target; });
  segmented('#history-range', 'range', (range) => { state.historyRange = range; loadHistory(); });
  $('#tweak-restore').addEventListener('click', async () => {
    try { await api('/api/power/restore', { method: 'POST' }); toast('Original settings restored'); }
    catch (err) { toast(err.message); }
    loadPower();
  });
  $('#max-fan').addEventListener('change', async (e) => {
    const on = e.target.checked;
    try {
      const r = await api('/api/fans/max', { method: 'POST', body: { on } });
      toast(r.max_fan ? 'Fans at maximum speed' : 'Fans back on automatic control');
    } catch (err) { e.target.checked = !on; toast(err.message); }
  });
  $('#energy-reset').addEventListener('click', () => api('/api/energy/reset', { method: 'POST' }).then(() => toast('Energy counters reset')).catch((e) => toast(e.message)));
  $('#speed-start').addEventListener('click', startSpeedTest);
  $('#diag-start').addEventListener('click', runDiagnostics);
  $('#cleanup-scan').addEventListener('click', scanCleanup);
  $('#cleanup-run').addEventListener('click', runCleanup);
  $('#stress-start').addEventListener('click', startStress);
  $('#history-export').addEventListener('click', exportHistory);
  $('#settings-form').addEventListener('submit', saveSettings);
  $('#settings-save').addEventListener('click', saveSettings);
  $('#settings-reset').addEventListener('click', async () => {
    if (!confirm('Reset every setting to its default?')) return;
    state.settings = (await api('/api/settings/reset', { method: 'POST' })).values;
    applyTheme(); renderSettings(); state.built = {}; $('#settings-status').textContent = 'Defaults restored.';
  });
  renderBenchCards();

  try {
    await loadSettings();
    state.live = await api('/api/live?seconds=900');
  } catch (err) { toast(err.message); }
  showView(location.hash.slice(1) || 'overview');
  connect();
}

init();
