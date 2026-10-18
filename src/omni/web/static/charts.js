// Small dependency-free canvas charts: a time-series line chart with crosshair tooltip,
// and a sparkline. Colours come from CSS role tokens so both themes work.
//
// Live charts use a fixed, continuously scrolling time window (like Task Manager): the
// x-axis always spans the last N seconds and new samples slide in from the right, so the
// chart never squeezes as the session gets longer. History charts fit their data instead.
'use strict';

const Charts = (() => {
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const live = new Set();
  let windowSeconds = 120;
  const LAG = 1.2; // seconds: draw slightly behind "now" so the newest sample slides in smoothly

  function readWindow() {
    try {
      const saved = Number(localStorage.getItem('omni-window'));
      if ([60, 120, 300, 900].includes(saved)) windowSeconds = saved;
    } catch { /* storage blocked */ }
  }
  readWindow();

  function setWindow(seconds) {
    windowSeconds = seconds;
    try { localStorage.setItem('omni-window', String(seconds)); } catch { /* ignore */ }
    live.forEach((c) => c.draw());
  }

  // One animation loop for every visible live chart (~30 fps; paused when the tab is hidden).
  let last = 0;
  function loop(now) {
    requestAnimationFrame(loop);
    if (document.hidden || now - last < 33 || document.documentElement.classList.contains('no-anim')) return;
    last = now;
    for (const c of live) if (c.el.offsetParent !== null) c.draw();
  }
  requestAnimationFrame(loop);

  function clockLabel(ts, withSeconds) {
    return new Date(ts * 1000).toLocaleTimeString([], withSeconds
      ? { hour: '2-digit', minute: '2-digit', second: '2-digit' } : { hour: '2-digit', minute: '2-digit' });
  }

  function timeLabel(ts, span) {
    const d = new Date(ts * 1000);
    if (span > 2 * 86400) return d.toLocaleDateString([], { day: 'numeric', month: 'short' });
    return clockLabel(ts, span < 600);
  }

  // Smallest "nice" tick step (1, 2, 2.5 or 5 x 10^k) that covers value in at most 5 ticks.
  function niceScale(value) {
    if (!(value > 0)) return { max: 1, ticks: 4 };
    for (let mag = Math.pow(10, Math.floor(Math.log10(value / 5))); ; mag *= 10) {
      for (const m of [1, 2, 2.5, 5]) {
        const step = m * mag;
        const ticks = Math.ceil(value / step);
        if (ticks <= 5) return { max: step * Math.max(ticks, 2), ticks: Math.max(ticks, 2) };
      }
    }
  }
  const niceMax = (value) => niceScale(value).max;

  // Time-axis tick step for a live window: about five labels.
  function timeStep(span) {
    for (const s of [10, 15, 20, 30, 60, 120, 180, 300, 600]) if (span / s <= 6) return s;
    return 900;
  }

  class LineChart {
    // series: [{key, label, color: '--series-1'}]
    // opts: {min, max, format(v), signed, emptyText, live (scrolling window), area (fill under one series)}
    constructor(el, series, opts = {}) {
      this.el = el;
      this.series = series;
      this.opts = opts;
      this.data = [];
      this.yMax = null;
      el.innerHTML = '';
      el.parentElement.querySelectorAll(':scope > .legend').forEach((n) => n.remove());
      if (series.length > 1) {
        const legend = document.createElement('div');
        legend.className = 'legend';
        for (const s of series) {
          const item = document.createElement('span');
          const key = document.createElement('span');
          key.className = 'key';
          key.style.background = `var(${s.color})`;
          item.append(key, document.createTextNode(s.label));
          legend.append(item);
        }
        el.before(legend);
      }
      this.canvas = document.createElement('canvas');
      this.tip = document.createElement('div');
      this.tip.className = 'chart-tip';
      this.tip.hidden = true;
      this.empty = document.createElement('div');
      this.empty.className = 'chart-empty';
      this.empty.textContent = opts.emptyText || 'Collecting data…';
      el.append(this.canvas, this.tip, this.empty);
      this.hoverX = null;
      this.canvas.addEventListener('pointermove', (e) => { this.hoverX = e.offsetX; this.draw(); });
      this.canvas.addEventListener('pointerleave', () => { this.hoverX = null; this.tip.hidden = true; this.draw(); });
      new ResizeObserver(() => this.draw()).observe(el);
      if (opts.live) live.add(this);
    }

    setData(points) {
      this.data = points || [];
      this.draw();
    }

    draw() {
      const { canvas, el, series, opts } = this;
      const width = el.clientWidth, height = el.clientHeight;
      if (!width || !height) return;
      const dpr = window.devicePixelRatio || 1;
      if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(height * dpr)) {
        canvas.width = Math.round(width * dpr);
        canvas.height = Math.round(height * dpr);
      }
      const ctx = canvas.getContext('2d');
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, width, height);

      // Time domain: a scrolling window for live charts, the data's own extent otherwise.
      let t0, t1, data = this.data;
      if (opts.live) {
        t1 = Date.now() / 1000 - LAG;
        t0 = t1 - windowSeconds;
        const firstInside = data.findIndex((p) => p.ts >= t0);
        data = firstInside < 0 ? [] : data.slice(Math.max(0, firstInside - 1));
      } else if (data.length) {
        t0 = data[0].ts; t1 = data[data.length - 1].ts;
      }

      const values = [];
      for (const p of data) for (const s of series) if (p[s.key] != null) values.push(p[s.key]);
      const enough = values.length > 1 && data.length > 1; // a line needs two points in time
      this.empty.hidden = enough;
      if (!enough) { this.tip.hidden = true; return; }
      const span = Math.max(1, t1 - t0);

      const fmt = opts.format || ((v) => v.toFixed(0));
      let min = opts.min ?? Math.min(0, ...values);
      let max = opts.max;
      let ticksY = 4;
      if (max == null) ({ max, ticks: ticksY } = niceScale(Math.max(...values) * 1.05 - min));
      if (opts.signed) {
        const extent = niceMax(Math.max(...values.map(Math.abs)) * 1.05);
        min = -extent; max = extent; ticksY = 4;
      }
      if (max === min) max = min + 1;
      const tickValues = Array.from({ length: ticksY + 1 }, (_, i) => min + ((max - min) * i) / ticksY);

      ctx.font = '11px system-ui, "Segoe UI", sans-serif';
      const labelW = Math.max(...tickValues.map((v) => ctx.measureText(fmt(v)).width)) + 10;
      const pad = { l: labelW, r: 8, t: 6, b: 20 };
      const plotW = width - pad.l - pad.r, plotH = height - pad.t - pad.b;
      const x = (ts) => pad.l + ((ts - t0) / span) * plotW;
      const y = (v) => pad.t + (1 - (v - min) / (max - min)) * plotH;

      // Recessive grid, ticks and labels.
      ctx.strokeStyle = css('--grid');
      ctx.fillStyle = css('--muted');
      ctx.lineWidth = 1;
      ctx.textAlign = 'right';
      ctx.textBaseline = 'middle';
      for (const v of tickValues) {
        const yy = Math.round(y(v)) + 0.5;
        ctx.beginPath(); ctx.moveTo(pad.l, yy); ctx.lineTo(width - pad.r, yy); ctx.stroke();
        ctx.fillText(fmt(v), pad.l - 6, yy);
      }
      if (min < 0 && max > 0) {
        ctx.strokeStyle = css('--axis');
        const yy = Math.round(y(0)) + 0.5;
        ctx.beginPath(); ctx.moveTo(pad.l, yy); ctx.lineTo(width - pad.r, yy); ctx.stroke();
      }
      ctx.textBaseline = 'alphabetic';
      if (opts.live) {
        // Ticks on round clock times that scroll with the data.
        const step = timeStep(span);
        ctx.textAlign = 'center';
        for (let ts = Math.ceil(t0 / step) * step; ts <= t1; ts += step) {
          const xx = x(ts);
          if (xx < pad.l + 24 || xx > width - pad.r - 24) continue;
          ctx.fillText(clockLabel(ts, step < 60), xx, height - 4);
        }
      } else {
        const ticks = Math.max(2, Math.min(6, Math.floor(plotW / 110)));
        for (let i = 0; i <= ticks; i++) {
          const ts = t0 + (span * i) / ticks;
          ctx.textAlign = i === 0 ? 'left' : i === ticks ? 'right' : 'center';
          ctx.fillText(timeLabel(ts, span), x(ts), height - 4);
        }
      }

      // Lines: 2px, gaps where data is missing, clipped to the plot so they slide in at the edge.
      ctx.save();
      ctx.beginPath();
      ctx.rect(pad.l, 0, plotW, height);
      ctx.clip();
      ctx.lineWidth = 2;
      ctx.lineJoin = 'round';
      for (const s of series) {
        const color = css(s.color);
        const pts = [];
        ctx.strokeStyle = color;
        ctx.beginPath();
        let drawing = false;
        for (const p of data) {
          const v = p[s.key];
          if (v == null) { drawing = false; continue; }
          const px = x(p.ts), py = y(Math.max(min, Math.min(max, v)));
          if (drawing) ctx.lineTo(px, py); else ctx.moveTo(px, py);
          drawing = true;
          pts.push([px, py]);
        }
        ctx.stroke();
        if (opts.area && series.length === 1 && pts.length > 1) {
          const base = y(Math.max(min, 0));
          const grad = ctx.createLinearGradient(0, pad.t, 0, base);
          grad.addColorStop(0, `${color}38`);
          grad.addColorStop(1, `${color}00`);
          ctx.fillStyle = grad;
          ctx.beginPath();
          ctx.moveTo(pts[0][0], base);
          for (const [px, py] of pts) ctx.lineTo(px, py);
          ctx.lineTo(pts[pts.length - 1][0], base);
          ctx.closePath();
          ctx.fill();
        }
      }
      ctx.restore();

      // Crosshair snaps to the nearest sample and lists every series.
      if (this.hoverX == null || this.hoverX < pad.l) { this.tip.hidden = true; return; }
      const targetTs = t0 + ((this.hoverX - pad.l) / plotW) * span;
      let nearest = data[0];
      for (const p of data) if (Math.abs(p.ts - targetTs) < Math.abs(nearest.ts - targetTs)) nearest = p;
      const cx = Math.round(x(nearest.ts)) + 0.5;
      if (cx < pad.l || cx > width - pad.r) { this.tip.hidden = true; return; }
      ctx.strokeStyle = css('--axis');
      ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(cx, pad.t); ctx.lineTo(cx, pad.t + plotH); ctx.stroke();
      for (const s of series) {
        const v = nearest[s.key];
        if (v == null) continue;
        ctx.fillStyle = css(s.color);
        ctx.strokeStyle = css('--surface');
        ctx.lineWidth = 2;
        ctx.beginPath(); ctx.arc(cx, y(Math.max(min, Math.min(max, v))), 4, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
      }

      this.tip.replaceChildren();
      const time = document.createElement('div');
      time.className = 'time';
      time.textContent = new Date(nearest.ts * 1000).toLocaleString([], span > 86400
        ? { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }
        : { hour: '2-digit', minute: '2-digit', second: '2-digit' });
      this.tip.append(time);
      for (const s of series) {
        const row = document.createElement('div');
        row.className = 'row';
        const key = document.createElement('span');
        key.className = 'key';
        key.style.background = `var(${s.color})`;
        const value = document.createElement('b');
        value.textContent = nearest[s.key] == null ? '—' : fmt(nearest[s.key]);
        const label = document.createElement('span');
        label.textContent = s.label;
        row.append(key, value, label);
        this.tip.append(row);
      }
      this.tip.hidden = false;
      const tipW = this.tip.offsetWidth;
      this.tip.style.left = `${cx + 12 + tipW > width ? cx - tipW - 12 : cx + 12}px`;
      this.tip.style.top = `${pad.t}px`;
    }
  }

  function sparkline(canvas, values, color, min = 0, max = null) {
    const width = canvas.clientWidth, height = canvas.clientHeight;
    if (!width) return;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = width * dpr;
    canvas.height = height * dpr;
    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    const pts = values.filter((v) => v != null);
    if (pts.length < 2) return;
    const hi = max ?? (Math.max(...pts) * 1.15 || 1);
    const lo = Math.min(min, ...pts);
    ctx.strokeStyle = css(color);
    ctx.lineWidth = 2;
    ctx.lineJoin = 'round';
    ctx.beginPath();
    values.forEach((v, i) => {
      if (v == null) return;
      const px = (i / (values.length - 1)) * (width - 2) + 1;
      const py = height - 2 - ((v - lo) / (hi - lo || 1)) * (height - 4);
      if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
    });
    ctx.stroke();
  }

  return { LineChart, sparkline, setWindow, getWindow: () => windowSeconds };
})();
