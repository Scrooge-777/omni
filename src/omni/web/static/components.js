// Visual building blocks. Each component is created once and then updated in place,
// so CSS transitions and number tweens animate between readings instead of jumping.
'use strict';

const UI = (() => {
  const SVG = 'http://www.w3.org/2000/svg';
  const motionOff = () => document.documentElement.classList.contains('no-anim');

  function svg(tag, attrs = {}) {
    const node = document.createElementNS(SVG, tag);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    return node;
  }

  function div(cls, text) {
    const node = document.createElement('div');
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  // Tween a number shown in `node` from its last value to `value`.
  function tween(node, value, format, duration = 650) {
    if (value == null || Number.isNaN(value)) { node.textContent = format(null); node._v = null; return; }
    const from = node._v;
    node._v = value;
    cancelAnimationFrame(node._raf);
    if (from == null || motionOff() || Math.abs(value - from) < 1e-9) { node.textContent = format(value); return; }
    const start = performance.now();
    const step = (now) => {
      const t = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - t, 3);
      node.textContent = format(from + (value - from) * eased);
      if (t < 1) node._raf = requestAnimationFrame(step);
    };
    node._raf = requestAnimationFrame(step);
  }

  // ---------------------------------------------------------------- radial gauge (270° arc)

  function arcPath(cx, cy, r, startDeg, endDeg) {
    const rad = (d) => ((d - 90) * Math.PI) / 180;
    const [x1, y1] = [cx + r * Math.cos(rad(startDeg)), cy + r * Math.sin(rad(startDeg))];
    const [x2, y2] = [cx + r * Math.cos(rad(endDeg)), cy + r * Math.sin(rad(endDeg))];
    return `M ${x1} ${y1} A ${r} ${r} 0 ${endDeg - startDeg > 180 ? 1 : 0} 1 ${x2} ${y2}`;
  }

  class Gauge {
    // opts: {label, max, format(v) -> text, unit, warn, danger, onClick}
    constructor(opts) {
      this.opts = opts;
      this.el = document.createElement(opts.onClick ? 'button' : 'div');
      this.el.className = 'gauge-card';
      if (opts.onClick) this.el.addEventListener('click', opts.onClick);
      const s = svg('svg', { viewBox: '0 0 120 120', class: 'gauge-svg', 'aria-hidden': 'true' });
      const d = arcPath(60, 60, 48, -135, 135);
      s.append(svg('path', { d, class: 'gauge-track', pathLength: 100 }));
      this.arc = svg('path', { d, class: 'gauge-value', pathLength: 100 });
      this.arc.style.strokeDasharray = '100 100';
      this.arc.style.strokeDashoffset = '100';
      s.append(this.arc);
      this.value = div('gauge-number');
      this.unit = div('gauge-unit', opts.unit || '');
      const center = div('gauge-center');
      center.append(this.value, this.unit);
      const wrap = div('gauge-wrap');
      wrap.append(s, center);
      this.label = div('gauge-label', opts.label);
      this.sub = div('gauge-sub');
      this.el.append(wrap, this.label, this.sub);
    }

    set(value, sub = '', max = this.opts.max) {
      const raw = value == null ? 0 : this.opts.scale ? this.opts.scale(value) : (value / max) * 100;
      const pct = Math.max(0, Math.min(100, raw));
      this.arc.style.strokeDashoffset = String(100 - pct);
      const level = value == null ? '' : value >= (this.opts.danger ?? Infinity) ? 'danger' : value >= (this.opts.warn ?? Infinity) ? 'warn' : '';
      this.el.dataset.level = level;
      tween(this.value, value, this.opts.format);
      this.sub.textContent = sub;
      this.el.setAttribute('aria-label', `${this.opts.label}: ${this.opts.format(value)} ${this.opts.unit || ''}. ${sub}`);
    }
  }

  // ---------------------------------------------------------------- 3D cylinder (fill level)

  class Cylinder {
    constructor(label, kind = '') {
      this.el = div(`cyl ${kind}`);
      const body = div('cyl-body');
      this.fill = div('cyl-fill');
      this.fill.append(div('cyl-fill-top'));
      body.append(this.fill, div('cyl-shine'));
      this.top = div('cyl-top');
      this.caption = div('cyl-caption');
      this.name = div('cyl-label', label);
      this.el.append(this.top, body, this.name, this.caption);
    }

    set(pct, caption, title) {
      const p = Math.max(0, Math.min(100, pct || 0));
      this.fill.style.height = `${p}%`;
      this.el.dataset.level = p >= 90 ? 'hot' : p >= 60 ? 'busy' : '';
      this.caption.textContent = caption;
      if (title) this.el.title = title;
    }
  }

  // ---------------------------------------------------------------- spinning fan

  const FAN_BLADE = 'M50 50 C 47 32 52 18 63 12 C 70 22 66 38 50 50 Z';

  class Fan {
    constructor(name) {
      this.el = div('fan-card');
      const s = svg('svg', { viewBox: '0 0 100 100', class: 'fan-svg', 'aria-hidden': 'true' });
      s.append(svg('circle', { cx: 50, cy: 50, r: 46, class: 'fan-ring' }));
      this.rotor = svg('g', { class: 'fan-rotor' });
      for (let i = 0; i < 7; i++) this.rotor.append(svg('path', { d: FAN_BLADE, transform: `rotate(${i * (360 / 7)} 50 50)`, class: 'fan-blade' }));
      this.rotor.append(svg('circle', { cx: 50, cy: 50, r: 9, class: 'fan-hub' }));
      s.append(this.rotor);
      this.rpm = div('fan-rpm');
      this.name = div('fan-name', name);
      this.meta = div('fan-meta');
      this.bar = div('bar');
      this.barFill = document.createElement('span');
      this.bar.append(this.barFill);
      this.el.append(s, this.rpm, this.name, this.bar, this.meta);
      this.spin = this.rotor.animate ? this.rotor.animate([{ transform: 'rotate(0deg)' }, { transform: 'rotate(360deg)' }],
        { duration: 1000, iterations: Infinity }) : null;
      if (this.spin) this.spin.playbackRate = 0;
    }

    // Visual speed follows RPM (scaled down so it reads as motion, not a blur).
    set(rpm, maxRpm, meta) {
      tween(this.rpm, rpm, (v) => (v == null ? '—' : `${Math.round(v).toLocaleString()} RPM`));
      const pct = rpm ? Math.min(100, (rpm / maxRpm) * 100) : 0;
      this.barFill.style.width = `${pct}%`;
      this.meta.textContent = meta;
      this.el.dataset.level = pct >= 85 ? 'hot' : rpm ? '' : 'idle';
      if (this.spin) this.spin.playbackRate = motionOff() ? 0 : (rpm || 0) / 1500;
    }
  }

  // ---------------------------------------------------------------- battery cylinder with liquid

  class BatteryCell {
    constructor() {
      this.el = div('battery-cell');
      const shell = div('battery-shell');
      this.liquid = div('battery-liquid');
      const wave = svg('svg', { viewBox: '0 0 200 20', preserveAspectRatio: 'none', class: 'battery-wave', 'aria-hidden': 'true' });
      wave.append(svg('path', { d: 'M0 10 Q 25 0 50 10 T 100 10 T 150 10 T 200 10 V 20 H 0 Z' }));
      this.liquid.append(wave);
      this.bolt = div('battery-bolt', '⚡');
      this.percent = div('battery-percent');
      shell.append(this.liquid, div('battery-gloss'), this.bolt, this.percent);
      this.el.append(div('battery-cap'), shell);
    }

    set(pct, charging) {
      this.liquid.style.height = `${Math.max(2, Math.min(100, pct || 0))}%`;
      this.el.dataset.level = pct <= 15 ? 'low' : pct <= 30 ? 'mid' : '';
      this.el.classList.toggle('charging', !!charging);
      tween(this.percent, pct, (v) => (v == null ? '—' : `${Math.round(v)}%`));
    }
  }

  // ---------------------------------------------------------------- donut (part-of-whole)

  class Donut {
    constructor(centerLabel) {
      this.el = div('donut');
      this.svg = svg('svg', { viewBox: '0 0 120 120', class: 'donut-svg', role: 'img' });
      this.svg.append(svg('circle', { cx: 60, cy: 60, r: 46, class: 'donut-track' }));
      this.ring = svg('g', { transform: 'rotate(-90 60 60)' });
      this.svg.append(this.ring);
      const center = div('donut-center');
      this.total = div('donut-total');
      center.append(this.total, div('donut-caption', centerLabel));
      const wrap = div('donut-wrap');
      wrap.append(this.svg, center);
      this.legend = div('donut-legend');
      this.el.append(wrap, this.legend);
      this.slices = new Map();
    }

    // parts: [{key, label, value, color, text}]
    set(parts, totalText) {
      const C = 2 * Math.PI * 46;
      const total = parts.reduce((s, p) => s + (p.value || 0), 0) || 1;
      const gap = parts.filter((p) => p.value > 0).length > 1 ? 1.2 : 0; // 2px-ish surface gap between slices
      let offset = 0;
      for (const p of parts) {
        let slice = this.slices.get(p.key);
        if (!slice) {
          slice = svg('circle', { cx: 60, cy: 60, r: 46, class: 'donut-slice' });
          slice.style.stroke = `var(${p.color})`;
          this.ring.append(slice);
          this.slices.set(p.key, slice);
        }
        const len = ((p.value || 0) / total) * C;
        slice.style.strokeDasharray = `${Math.max(0, len - gap)} ${C}`;
        slice.style.strokeDashoffset = String(-offset);
        offset += len;
      }
      this.total.textContent = totalText;
      this.svg.setAttribute('aria-label', parts.map((p) => `${p.label} ${p.text}`).join(', '));
      this.legend.replaceChildren(...parts.map((p) => {
        const row = div('donut-row');
        const key = div('donut-key');
        key.style.background = `var(${p.color})`;
        const value = document.createElement('b');
        value.textContent = p.text;
        const label = document.createElement('span');
        label.textContent = p.label;
        row.append(key, value, label);
        return row;
      }));
    }
  }

  // ---------------------------------------------------------------- stacked bar (part-of-whole, one row)

  class StackBar {
    constructor() {
      this.el = div('stackbar');
      this.track = div('stackbar-track');
      this.legend = div('stackbar-legend');
      this.el.append(this.track, this.legend);
      this.segments = new Map();
    }

    // parts: [{key, label, value, color, text}]
    set(parts, total) {
      const sum = total || parts.reduce((s, p) => s + (p.value || 0), 0) || 1;
      for (const p of parts) {
        let seg = this.segments.get(p.key);
        if (!seg) {
          seg = div('stackbar-seg');
          seg.style.background = `var(${p.color})`;
          this.track.append(seg);
          this.segments.set(p.key, seg);
        }
        seg.style.width = `${((p.value || 0) / sum) * 100}%`;
        seg.title = `${p.label}: ${p.text}`;
      }
      this.legend.replaceChildren(...parts.map((p) => {
        const row = div('stackbar-item');
        const key = div('donut-key');
        key.style.background = `var(${p.color})`;
        const text = div();
        const value = document.createElement('b');
        value.textContent = p.text;
        text.append(value, document.createTextNode(` ${p.label}`));
        row.append(key, text);
        return row;
      }));
    }
  }

  // ---------------------------------------------------------------- column chart (few labelled bars)

  class Columns {
    constructor(labels, color = '--series-1') {
      this.el = div('columns');
      this.cols = labels.map((label) => {
        const col = div('column');
        const value = div('column-value');
        const track = div('column-track');
        const fill = div('column-fill');
        fill.style.background = `var(${color})`;
        track.append(fill);
        col.append(value, track, div('column-label', label));
        this.el.append(col);
        return { value, fill, col };
      });
    }

    set(values, format, titles = []) {
      const max = Math.max(...values, 1);
      values.forEach((v, i) => {
        const c = this.cols[i];
        c.fill.style.height = `${(v / max) * 100}%`;
        c.value.textContent = format(v);
        if (titles[i]) c.col.title = titles[i];
      });
    }
  }

  return { Gauge, Cylinder, Fan, BatteryCell, Donut, StackBar, Columns, tween, div };
})();
