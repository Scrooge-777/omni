// Small dependency-free canvas charts: a time-series line chart with crosshair tooltip,
// and a sparkline. Colours come from CSS role tokens so both themes work.
'use strict';

const Charts = (() => {
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

  function timeLabel(ts, span) {
    const d = new Date(ts * 1000);
    if (span > 2 * 86400) return d.toLocaleDateString([], { day: 'numeric', month: 'short' });
    if (span < 600) return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }

  function niceMax(value) {
    if (value <= 0) return 1;
    const mag = Math.pow(10, Math.floor(Math.log10(value)));
    for (const step of [1, 2, 2.5, 5, 10]) if (value <= step * mag) return step * mag;
    return 10 * mag;
  }

  class LineChart {
    // series: [{key, label, color: '--series-1'}]; opts: {min, max, format(v), signed}
    constructor(el, series, opts = {}) {
      this.el = el;
      this.series = series;
      this.opts = opts;
      this.data = [];
      el.innerHTML = '';
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
    }

    setData(points) {
      this.data = points || [];
      this.draw();
    }

    draw() {
      const { canvas, el, data, series, opts } = this;
      const width = el.clientWidth, height = el.clientHeight;
      if (!width || !height) return;
      const dpr = window.devicePixelRatio || 1;
      canvas.width = width * dpr;
      canvas.height = height * dpr;
      const ctx = canvas.getContext('2d');
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, width, height);

      const values = [];
      for (const p of data) for (const s of series) if (p[s.key] != null) values.push(p[s.key]);
      const enough = values.length > 1 && data.length > 1; // a line needs two points in time
      this.empty.hidden = enough;
      if (!enough) { this.tip.hidden = true; return; }

      const fmt = opts.format || ((v) => v.toFixed(0));
      let min = opts.min ?? Math.min(0, ...values);
      let max = opts.max ?? niceMax(Math.max(...values) * 1.1);
      if (opts.signed) {
        const extent = niceMax(Math.max(...values.map(Math.abs)) * 1.1);
        min = -extent; max = extent;
      }
      if (max === min) max = min + 1;

      ctx.font = '11px system-ui, "Segoe UI", sans-serif';
      const labelW = Math.max(ctx.measureText(fmt(max)).width, ctx.measureText(fmt(min)).width) + 10;
      const pad = { l: labelW, r: 8, t: 6, b: 20 };
      const plotW = width - pad.l - pad.r, plotH = height - pad.t - pad.b;
      const t0 = data[0].ts, t1 = data[data.length - 1].ts, span = Math.max(1, t1 - t0);
      const x = (ts) => pad.l + ((ts - t0) / span) * plotW;
      const y = (v) => pad.t + (1 - (v - min) / (max - min)) * plotH;

      // Recessive grid, ticks and labels.
      ctx.strokeStyle = css('--grid');
      ctx.fillStyle = css('--muted');
      ctx.lineWidth = 1;
      ctx.textAlign = 'right';
      ctx.textBaseline = 'middle';
      for (let i = 0; i <= 4; i++) {
        const v = min + ((max - min) * i) / 4;
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
      ctx.textAlign = 'center';
      const ticks = Math.max(2, Math.min(6, Math.floor(plotW / 110)));
      for (let i = 0; i <= ticks; i++) {
        const ts = t0 + (span * i) / ticks;
        ctx.textAlign = i === 0 ? 'left' : i === ticks ? 'right' : 'center';
        ctx.fillText(timeLabel(ts, span), x(ts), height - 4);
      }

      // Lines: 2px, gaps where data is missing.
      ctx.lineWidth = 2;
      ctx.lineJoin = 'round';
      for (const s of series) {
        ctx.strokeStyle = css(s.color);
        ctx.beginPath();
        let drawing = false;
        for (const p of data) {
          const v = p[s.key];
          if (v == null) { drawing = false; continue; }
          const px = x(p.ts), py = y(Math.max(min, Math.min(max, v)));
          if (drawing) ctx.lineTo(px, py); else ctx.moveTo(px, py);
          drawing = true;
        }
        ctx.stroke();
      }

      // Crosshair snaps to the nearest sample and lists every series.
      if (this.hoverX == null || this.hoverX < pad.l) { this.tip.hidden = true; return; }
      const targetTs = t0 + ((this.hoverX - pad.l) / plotW) * span;
      let nearest = data[0];
      for (const p of data) if (Math.abs(p.ts - targetTs) < Math.abs(nearest.ts - targetTs)) nearest = p;
      const cx = Math.round(x(nearest.ts)) + 0.5;
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

  return { LineChart, sparkline };
})();
