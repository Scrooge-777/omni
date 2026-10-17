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
  if (!res.ok) throw new Error((data && data.detail) || `Request failed (${res.status})`);
  return data;
}

let toastTimer;
function toast(message) {
  const el = $('#toast');
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 4000);
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
  watts(w, digits = 1) { return w == null ? '—' : `${w.toFixed(digits)} W`; },
  freq(mhz) { return mhz == null ? '—' : mhz >= 1000 ? `${(mhz / 1000).toFixed(2)} GHz` : `${Math.round(mhz)} MHz`; },
  pct(v, digits = 0) { return v == null ? '—' : `${v.toFixed(digits)} %`; },
  duration(s) {
    if (s == null || s < 0) return '—';
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
    return h ? `${h} h ${String(m).padStart(2, '0')} min` : `${m} min`;
  },
  time(ts) { return new Date(ts * 1000).toLocaleString(); },
};

// ------------------------------------------------------------------ charts

// Watts: one decimal for small values so axis ticks like 0.5 W don't all round to "1 W".
const wattsAxis = (v) => `${Math.abs(v) < 10 && v % 1 ? v.toFixed(1) : v.toFixed(0)} W`;

const CHARTS = {
  usage: [[['cpu_pct', 'CPU'], ['gpu_pct', 'GPU'], ['ram_pct', 'Memory']], { min: 0, max: 100, format: (v) => `${v.toFixed(0)}%` }],
  temps: [[['cpu_temp_c', 'CPU'], ['gpu_temp_c', 'GPU']], { format: fmt.tempAxis, temp: true }],
  'cpu-usage': [[['cpu_pct', 'CPU']], { min: 0, max: 100, format: (v) => `${v.toFixed(0)}%` }],
  'cpu-clock': [[['cpu_mhz', 'Clock']], { format: (v) => `${(v / 1000).toFixed(1)} GHz` }],
  'cpu-temp': [[['cpu_temp_c', 'CPU']], { format: fmt.tempAxis, temp: true }],
  'gpu-usage': [[['gpu_pct', 'GPU']], { min: 0, max: 100, format: (v) => `${v.toFixed(0)}%` }],
  'gpu-power': [[['gpu_w', 'Power']], { format: wattsAxis }],
  'gpu-temp': [[['gpu_temp_c', 'GPU']], { format: fmt.tempAxis, temp: true }],
  'batt-pct': [[['batt_pct', 'Charge']], { min: 0, max: 100, format: (v) => `${v.toFixed(0)}%` }],
  'batt-power': [[['batt_w', 'Battery power']], { signed: true, format: wattsAxis }],
  'batt-capacity': [[['full_wh', 'Full charge'], ['design_wh', 'Design']], {
    min: 0, format: (v) => `${v.toFixed(0)} Wh`, emptyText: 'Needs readings from at least two days. OMNI records one per day.' }],
  'disk-io': [[['disk_read_bps', 'Read'], ['disk_write_bps', 'Write']], { format: (v) => fmt.rate(v) }],
  'net-io': [[['net_down_bps', 'Download'], ['net_up_bps', 'Upload']], { format: (v) => fmt.rate(v) }],
  stress: [[['cpu_temp_c', 'CPU'], ['gpu_temp_c', 'GPU']], { format: fmt.tempAxis, temp: true }],
  'h-usage': [[['cpu_pct', 'CPU'], ['gpu_pct', 'GPU'], ['ram_pct', 'Memory']], { min: 0, max: 100, format: (v) => `${v.toFixed(0)}%` }],
  'h-temps': [[['cpu_temp_c', 'CPU'], ['gpu_temp_c', 'GPU']], { format: fmt.tempAxis, temp: true }],
  'h-batt': [[['batt_pct', 'Charge']], { min: 0, max: 100, format: (v) => `${v.toFixed(0)}%` }],
  'h-gpu-power': [[['gpu_w', 'Power']], { format: wattsAxis }],
};

function chart(name) {
  if (!state.charts[name]) {
    const [series, opts] = CHARTS[name];
    const node = document.querySelector(`[data-chart="${name}"]`);
    state.charts[name] = new Charts.LineChart(node, series.map(([key, label], i) => ({ key, label, color: `--series-${i + 1}` })), opts);
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
  const b = snap.battery || {}, g = snap.gpu || {}, s = snap.storage || {}, n = snap.network || {};
  return {
    ts: snap.ts, cpu_pct: snap.cpu.usage_pct, cpu_temp_c: snap.cpu.temp_c, cpu_mhz: snap.cpu.clock_mhz,
    ram_pct: snap.memory.usage_pct, gpu_pct: g.util_pct, gpu_temp_c: g.temp_c, gpu_w: g.power_w,
    batt_pct: b.percent, batt_w: b.power_w, disk_read_bps: s.read_bps, disk_write_bps: s.write_bps,
    net_down_bps: n.down_bps, net_up_bps: n.up_bps,
  };
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
    if (state.live.length > 600) state.live.splice(0, state.live.length - 600);
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

const ON_SHOW = {
  power: loadPower,
  bench: loadBenchHistory,
  history: loadHistory,
  settings: renderSettings,
  battery: loadCapacity,
};

function showView(name) {
  if (!$(`#view-${name}`)) name = 'overview';
  state.view = name;
  $$('.view').forEach((v) => v.classList.toggle('active', v.id === `view-${name}`));
  $$('.sidebar button').forEach((b) => b.classList.toggle('active', b.dataset.view === name));
  $('#sidebar').classList.remove('open');
  if (location.hash !== `#${name}`) history.replaceState(null, '', `#${name}`);
  ON_SHOW[name]?.();
  render();
}

// ------------------------------------------------------------------ rendering

function render() {
  const snap = state.snap;
  if (!snap) return;
  renderAlerts(snap.alerts || []);
  const renderer = RENDERERS[state.view];
  if (renderer) renderer(snap);
}

function renderAlerts(alerts) {
  $('#alerts').replaceChildren(...alerts.map((a) => el('div', { class: `alert ${a.level}` },
    el('span', { class: 'icon', 'aria-hidden': 'true' }, a.level === 'info' ? 'ℹ' : '⚠'),
    el('span', {}, a.message))));
}

function tile(label, value, sub, view, key, color = '--series-1', max = null) {
  const canvas = el('canvas');
  const node = el('button', { class: 'tile', onclick: () => showView(view) },
    el('div', { class: 'label' }, label), el('div', { class: 'value' }, value), el('div', { class: 'sub' }, sub), canvas);
  requestAnimationFrame(() => Charts.sparkline(canvas, state.live.slice(-120).map((p) => p[key]), color, 0, max));
  return node;
}

const RENDERERS = {
  overview(snap) {
    const { cpu, gpu, battery: b, memory: m, storage: s, network: n } = snap;
    const tiles = [
      tile('CPU', fmt.pct(cpu.usage_pct), `${fmt.temp(cpu.temp_c)} · ${fmt.freq(cpu.clock_mhz)}`, 'cpu', 'cpu_pct', '--series-1', 100),
      !gpu.available ? tile('GPU', '—', gpu.error || 'Not available', 'gpu', 'gpu_pct')
        : gpu.paused ? tile('GPU', 'Paused', 'Not read on battery so it can sleep', 'gpu', 'gpu_pct', '--series-1', 100)
        : gpu.asleep ? tile('GPU', 'Sleeping', `Powered down · ${fmt.temp(gpu.temp_c)}`, 'gpu', 'gpu_pct', '--series-1', 100)
        : tile('GPU', fmt.pct(gpu.util_pct), `${fmt.temp(gpu.temp_c)} · ${fmt.watts(gpu.power_w)}`, 'gpu', 'gpu_pct', '--series-1', 100),
      b.present
        ? tile('Battery', fmt.pct(b.percent), `${b.state} · ${fmt.watts(Math.abs(b.power_w || 0))}${b.time_left_s ? ` · ${fmt.duration(b.time_left_s)}` : ''}`, 'battery', 'batt_pct', '--series-1', 100)
        : tile('Battery', '—', 'No battery', 'battery', 'batt_pct'),
      tile('Memory', fmt.pct(m.usage_pct), `${fmt.bytes(m.used)} of ${fmt.bytes(m.total)}`, 'storage', 'ram_pct', '--series-1', 100),
      tile('Disk', fmt.rate((s.read_bps || 0) + (s.write_bps || 0)), `Read ${fmt.rate(s.read_bps)} · Write ${fmt.rate(s.write_bps)}`, 'storage', 'disk_read_bps'),
      tile('Network', fmt.rate(n.down_bps), `Down ${fmt.rate(n.down_bps)} · Up ${fmt.rate(n.up_bps)}`, 'network', 'net_down_bps'),
    ];
    $('#tiles').replaceChildren(...tiles);
    setChart('usage', state.live);
    setChart('temps', state.live);
  },

  cpu(snap) {
    const c = snap.cpu;
    kv($('#cpu-kv'), [
      ['Processor', c.name], ['Cores / threads', `${c.cores} / ${c.threads}`], ['Usage', fmt.pct(c.usage_pct, 1)],
      ['Clock now', fmt.freq(c.clock_mhz)], ['Base clock', fmt.freq(c.base_mhz)], ['Temperature', fmt.temp(c.temp_c, 1)],
      ...Object.entries(c.zones || {}).map(([zone, t]) => [`Sensor ${zone}`, fmt.temp(t, 1)]),
    ]);
    $('#cores').replaceChildren(...c.per_core_pct.map((v, i) => el('div', { class: 'core' },
      `#${i} · ${v.toFixed(0)}%`, el('div', { class: `bar${v > 90 ? ' warn' : ''}` }, el('span', { style: `width:${v}%` })))));
    setChart('cpu-usage', state.live);
    setChart('cpu-clock', state.live);
    setChart('cpu-temp', state.live);
  },

  gpu(snap) {
    const g = snap.gpu;
    if (!g.available) {
      kv($('#gpu-kv'), [['Status', g.error || 'No NVIDIA GPU found']]);
      $('#gpu-note').textContent = 'OMNI reads NVIDIA GPUs through the driver (NVML).';
      return;
    }
    kv($('#gpu-kv'), [
      ['GPU', g.name], ['Driver', g.driver], ['Load', fmt.pct(g.util_pct)], ['Temperature', fmt.temp(g.temp_c)],
      ['Power', `${fmt.watts(g.power_w)} of ${fmt.watts(g.power_limit_w, 0)}`], ['Core clock', `${fmt.freq(g.clock_mhz)} (max ${fmt.freq(g.max_clock_mhz)})`],
      ['Memory clock', fmt.freq(g.mem_clock_mhz)], ['VRAM', `${fmt.bytes(g.vram_used)} of ${fmt.bytes(g.vram_total)}`],
      ['Performance state', g.pstate], ['Limited by', g.limits && g.limits.length ? g.limits.join(', ') : 'Nothing'],
    ]);
    $('#gpu-note').textContent = g.paused
      ? 'On battery, OMNI stops reading the GPU so it can sleep. Turn on "Poll GPU on battery" in Settings to change this.'
      : g.asleep ? 'The GPU is in a low-power sleep state.' : '';
    setChart('gpu-usage', state.live);
    setChart('gpu-power', state.live);
    setChart('gpu-temp', state.live);
  },

  battery(snap) {
    const b = snap.battery;
    if (!b.present) {
      $('#battery-hero').replaceChildren(el('p', {}, 'No battery detected.'));
      return;
    }
    $('#battery-hero').replaceChildren(gauge(b.percent), el('div', { class: 'gauge-text' },
      el('div', { class: 'big' }, fmt.pct(b.percent)),
      el('div', {}, `${b.state[0].toUpperCase()}${b.state.slice(1)}`),
      el('div', { class: 'hint' }, b.time_left_s ? `${fmt.duration(b.time_left_s)} ${b.state === 'charging' ? 'to full' : 'left'}` : ''),
      b.grade ? el('span', { class: `badge ${b.health_pct >= 85 ? 'good' : 'bad'}` }, `${b.health_pct >= 85 ? '✓' : '!'} Health: ${b.grade}`) : null));
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

  storage(snap) {
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

  network(snap) {
    const n = snap.network;
    kv($('#net-kv'), [['Download', fmt.rate(n.down_bps)], ['Upload', fmt.rate(n.up_bps)],
      ['Received since boot', fmt.bytes(n.total_down)], ['Sent since boot', fmt.bytes(n.total_up)]]);
    setChart('net-io', state.live);
  },

  processes(snap) {
    $('#procs').replaceChildren(
      el('tr', {}, el('th', {}, 'Name'), el('th', { class: 'num' }, 'PID'), el('th', { class: 'num' }, 'CPU'), el('th', { class: 'num' }, 'Memory'), el('th', {}, '')),
      ...snap.processes.map((p) => el('tr', {},
        el('td', { class: 'name', title: p.name }, p.name), el('td', { class: 'num' }, p.pid),
        el('td', { class: 'num' }, `${p.cpu_pct.toFixed(1)} %`), el('td', { class: 'num' }, fmt.bytes(p.mem)),
        el('td', {}, el('button', { class: 'btn danger', onclick: () => endProcess(p) }, 'End')))));
  },

  bench() { updateStressChart(); },
};

function gauge(pct) {
  const NS = 'http://www.w3.org/2000/svg';
  const r = 54, c = 2 * Math.PI * r;
  const svg = document.createElementNS(NS, 'svg');
  svg.setAttribute('viewBox', '0 0 132 132');
  svg.setAttribute('class', 'gauge');
  svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label', `Charge ${Math.round(pct)} percent`);
  for (const [color, dash] of [['--surface-2', null], ['--series-1', (c * Math.max(0, Math.min(100, pct))) / 100]]) {
    const ring = document.createElementNS(NS, 'circle');
    for (const [k, v] of Object.entries({ cx: 66, cy: 66, r, fill: 'none', 'stroke-width': 12 })) ring.setAttribute(k, v);
    ring.style.stroke = `var(${color})`;
    if (dash != null) {
      ring.setAttribute('stroke-linecap', 'round');
      ring.setAttribute('stroke-dasharray', `${dash} ${c}`);
      ring.setAttribute('transform', 'rotate(-90 66 66)');
    }
    svg.append(ring);
  }
  return svg;
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

// ------------------------------------------------------------------ power

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

async function runJob(startPath, body, onUpdate) {
  let job = await api(startPath, { method: 'POST', body });
  onUpdate(job);
  while (job.status === 'running') {
    await new Promise((r) => setTimeout(r, 700));
    job = await api(`/api/jobs/${job.id}`);
    onUpdate(job);
  }
  return job;
}

const BENCHES = {
  cpu: ['CPU', 'Single-core speed, then every thread at once in separate processes.', (r) => [
    ['Single-core', `${r.single_score.toLocaleString()} pts`], ['Multi-core', `${r.multi_score.toLocaleString()} pts`],
    ['Scaling', `${r.scaling}× on ${r.threads} threads`]]],
  memory: ['Memory', 'Native memcpy / memset bandwidth on a 512 MiB buffer.', (r) => [
    ['Copy', fmt.rate(r.copy_bps)], ['Write', fmt.rate(r.write_bps)]]],
  disk: ['SSD', 'Unbuffered 1 GiB write and read, then random 4 KiB reads (QD1). Uses your temp folder.', (r) => [
    ['Sequential read', fmt.rate(r.seq_read_bps)], ['Sequential write', fmt.rate(r.seq_write_bps)],
    ['Random 4K', `${r.rand4k_iops.toLocaleString()} IOPS`], ['4K latency', `${r.rand4k_latency_ms_avg} ms avg · ${r.rand4k_latency_ms_p99} ms p99`]]],
  gpu: ['GPU', 'FP32 fused multiply-add throughput through CUDA.', (r) => [
    ['FP32', `${r.fp32_tflops} TFLOPS`], ['Device', r.device]]],
};

function renderBenchCards() {
  $('#bench-cards').replaceChildren(...Object.entries(BENCHES).map(([kind, [title, desc]]) => {
    const progress = el('div', { class: 'progress', hidden: true }, el('span'));
    const status = el('div', { class: 'hint' });
    const result = el('div', { class: 'bench-result' });
    const stop = el('button', { class: 'btn', hidden: true }, 'Stop');
    const run = el('button', { class: 'btn primary' }, 'Run');
    run.addEventListener('click', async () => {
      run.disabled = true; progress.hidden = false; result.replaceChildren();
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
          const rows = BENCHES[kind][2](job.result);
          result.replaceChildren(...rows.map(([k, v], i) => el('div', {}, i === 0 ? el('div', { class: 'big' }, v) : el('b', {}, v), ' ', el('span', { class: 'hint' }, k))));
          status.textContent = 'Done';
          loadBenchHistory();
        }
      } catch (err) { status.textContent = err.message; }
      run.disabled = false; stop.hidden = true; progress.hidden = true;
    });
    return el('div', { class: 'card' }, el('h2', {}, title), el('p', { class: 'hint' }, desc), result, progress, status,
      el('div', { class: 'toolbar' }, run, stop));
  }));
}

async function loadBenchHistory() {
  let rows;
  try { rows = await api('/api/bench/results'); } catch { return; }
  const summary = (kind, r) => {
    const def = BENCHES[kind.replace('bench-', '')];
    return def ? def[2](r).map(([k, v]) => `${k}: ${v}`).join(' · ') : '';
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
  const metrics = 'cpu_pct,gpu_pct,ram_pct,cpu_temp_c,gpu_temp_c,batt_pct,gpu_w';
  try {
    const data = await api(`/api/history?metrics=${metrics}&range=${state.historyRange}`);
    const points = data.ts.map((ts, i) => {
      const p = { ts };
      for (const key of metrics.split(',')) p[key] = data[key][i];
      return p;
    });
    ['h-usage', 'h-temps', 'h-batt', 'h-gpu-power'].forEach((name) => setChart(name, points));
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
  units: ['Units', {
    temperature: ['Temperature', 'C = Celsius (metric), F = Fahrenheit.'],
    data: ['Data sizes', 'si = metric (1 GB = 1000 MB), iec = binary (1 GiB = 1024 MiB).'],
    energy: ['Battery energy', 'Wh or mWh.'],
  }],
  gpu: ['GPU', {
    enabled: ['Read the GPU', ''],
    poll_on_battery: ['Poll GPU on battery', 'Off lets an Optimus GPU sleep on battery and saves power.'],
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
  const form = $('#settings-form');
  form.replaceChildren(...Object.entries(state.schema).map(([group, keys]) => {
    const [groupLabel, labels] = SETTING_TEXT[group] || [group, {}];
    return el('fieldset', {}, el('legend', {}, groupLabel), ...Object.entries(keys).map(([key, spec]) => {
      const [label, desc] = labels[key] || [key, ''];
      const value = state.settings[group][key];
      const id = `s-${group}-${key}`;
      let input;
      if (typeof spec.default === 'boolean') input = el('input', { type: 'checkbox', id, checked: value });
      else if (Array.isArray(spec.allowed) && typeof spec.default === 'string') {
        input = el('select', { id }, ...spec.allowed.map((opt) => el('option', { value: opt, selected: opt === value }, opt)));
      } else if (typeof spec.default === 'number') {
        input = el('input', { type: 'number', id, value, min: spec.allowed[0], max: spec.allowed[1], step: Number.isInteger(spec.default) ? 1 : 0.5 });
      } else input = el('input', { type: 'text', id, value });
      input.dataset.group = group;
      input.dataset.key = key;
      return el('div', { class: 'setting' }, el('label', { for: id }, el('div', {}, label), desc ? el('div', { class: 'desc' }, desc) : null), input);
    }));
  }));
  api('/api/info').then((info) => kv($('#about'), [
    ['Version', info.version], ['Running as administrator', info.admin ? 'Yes' : 'No (some cleanup needs it)'],
    ['GPU', info.gpu || info.gpu_error], ['Data folder', info.data_dir],
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
    render();
  } catch (err) { $('#settings-status').textContent = err.message; }
}

function applyTheme() {
  const theme = state.settings?.general.theme || 'system';
  if (theme === 'system') delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = theme;
  try { localStorage.setItem('omni-theme', theme); } catch { /* ignore */ }
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

async function init() {
  $$('.sidebar button').forEach((b) => b.addEventListener('click', () => showView(b.dataset.view)));
  $('#menu-btn').addEventListener('click', () => $('#sidebar').classList.toggle('open'));
  $('#theme-btn').addEventListener('click', cycleTheme);
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
  $('#cleanup-scan').addEventListener('click', scanCleanup);
  $('#cleanup-run').addEventListener('click', runCleanup);
  $('#stress-start').addEventListener('click', startStress);
  $('#history-export').addEventListener('click', exportHistory);
  $('#settings-form').addEventListener('submit', saveSettings);
  $('#settings-save').addEventListener('click', saveSettings);
  $('#settings-reset').addEventListener('click', async () => {
    if (!confirm('Reset every setting to its default?')) return;
    state.settings = (await api('/api/settings/reset', { method: 'POST' })).values;
    applyTheme(); renderSettings(); $('#settings-status').textContent = 'Defaults restored.';
  });
  renderBenchCards();

  try {
    await loadSettings();
    state.live = await api('/api/live?seconds=600');
  } catch (err) { toast(err.message); }
  showView(location.hash.slice(1) || 'overview');
  connect();
}

init();
