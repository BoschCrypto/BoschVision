/* VELOCITY mind renderer.
 *
 *   const mind = VelocityMind.mount(container, {mode: "live" | "replay", symbol, entry});
 *   mind.push(frame)        one bar from velocity/mind.py's FrameBuilder
 *   mind.price(px, secs)    live price between bars
 *   mind.play(frames)       replay mode: step through recorded frames
 *   mind.destroy()
 *
 * Each agent is a sphere of orbiting arcs. How fast it turns and how bright
 * it burns is the strength of that agent's current view; beams between
 * spheres carry what one agent is telling another.
 */
(function () {
  "use strict";
  const AG = {
    QUANT:    { role: "technical analysis", rgb: [76, 201, 255] },
    CHARTIST: { role: "chart structure",    rgb: [190, 130, 255] },
    SENTINEL: { role: "news",               rgb: [90, 240, 160] },
    WARDEN:   { role: "risk",               rgb: [255, 95, 95] },
    ARBITER:  { role: "decides",            rgb: [255, 184, 40] },
  };
  const ORDER = ["QUANT", "CHARTIST", "ARBITER", "WARDEN", "SENTINEL"];
  // [from, to, bend]: bend bows the beam so crossing beams stay apart.
  const LINKS = [
    ["QUANT", "CHARTIST", -0.22], ["QUANT", "ARBITER", 0.10], ["CHARTIST", "ARBITER", -0.10],
    ["SENTINEL", "QUANT", -0.18], ["SENTINEL", "ARBITER", 0.12],
    ["ARBITER", "WARDEN", 0.14], ["WARDEN", "ARBITER", 0.14],
  ];
  const L = {}; LINKS.forEach((l, i) => { L[l[0] + ">" + l[1]] = i; });

  function rng(seed) { let s = seed >>> 0; return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296); }
  const rgba = (c, a) => "rgba(" + c[0] + "," + c[1] + "," + c[2] + "," + Math.max(0, Math.min(1, a)).toFixed(3) + ")";
  const sgn = (v, d) => (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v).toFixed(d == null ? 2 : d);
  const money = (v) => (v < 0 ? "−$" : "$") + Math.abs(v).toFixed(2);
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };

  function makeOrb(name, seed) {
    const r = rng(seed), big = name === "ARBITER", rings = [];
    const n = big ? 30 : 20;
    for (let i = 0; i < n; i++) {
      const inner = r() < 0.3;
      const arcs = [], k = 2 + Math.floor(r() * 4);
      for (let j = 0; j < k; j++) arcs.push([r() * Math.PI * 2, 0.25 + r() * 1.3, r() < 0.25 ? 2.2 : 1]);
      rings.push({ inc: r() * Math.PI, node: r() * Math.PI * 2, rad: inner ? 0.35 + r() * 0.3 : 0.72 + r() * 0.28,
                   spd: (r() - 0.5) * 1.1, ph: r() * 6.283, arcs: arcs, ticks: r() < 0.35 });
    }
    const sparks = [];
    for (let i = 0; i < (big ? 90 : 55); i++) {
      const u = r() * 2 - 1, th = r() * 6.283, s = Math.sqrt(1 - u * u);
      sparks.push([s * Math.cos(th), u, s * Math.sin(th), r() * 6.283, 0.6 + r() * 0.5]);
    }
    return { name: name, rgb: AG[name].rgb, rings: rings, sparks: sparks, act: 0.15, target: 0.15,
             flare: 0, dormant: false, spin: r() * 6.283, x: 0, y: 0, R: 10, label: "", sub: "" };
  }

  function mount(host, opts) {
    opts = opts || {};
    const ENTRY = opts.entry || 0.28;
    const still = !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);

    // ---- DOM ----------------------------------------------------------
    const root = el("div", "vm");
    const hud = el("div", "vm-hud");
    const badge = el("span", "vm-badge", "…");
    const kv = (k) => { const w = el("span", "vm-kv"); w.append(el("span", "vm-k", k)); const v = el("span", "vm-v", "—"); w.append(v); hud.append(w); return v; };
    hud.append(badge);
    const hSym = kv("Market"), hPx = kv("Price"), hTime = kv("Bar"), hPos = kv("Position"),
          hOpen = kv("Open P&L"), hPnl = kv("Closed P&L"), hNext = kv(opts.mode === "live" ? "Next read" : "Speed");
    hSym.textContent = opts.symbol || "—";
    const ctl = el("span", "vm-ctl"); hud.append(ctl);
    const canvas = el("canvas", "vm-canvas");
    canvas.setAttribute("role", "img");
    canvas.setAttribute("aria-label", "Five glowing spheres, one per agent, with beams of light where one agent passes information to another. The same readings are listed as text below.");
    const cards = el("div", "vm-cards"), card = {};
    ORDER.forEach((name) => {
      const c = el("div", "vm-card"), top = el("div", "vm-card-top"), dot = el("span", "vm-dot");
      dot.style.color = rgba(AG[name].rgb, 1);
      top.append(dot, el("span", "vm-name", name), el("span", "vm-role", AG[name].role));
      const state = el("div", "vm-state", "—"), d1 = el("div", "vm-detail", ""), d2 = el("div", "vm-detail", "");
      c.append(top, state, d1, d2); cards.append(c); card[name] = { state: state, d1: d1, d2: d2 };
    });
    const log = el("div", "vm-log"), list = el("ul", "vm-log-list");
    const empty = el("li", "vm-log-item vm-log-empty", "Nothing has passed between the agents yet.");
    list.append(empty);
    log.append(el("div", "vm-log-title", "What just happened between them"), list);
    root.append(hud, canvas, cards, log);
    host.replaceChildren(root);
    const ctx = canvas.getContext("2d");

    // ---- scene --------------------------------------------------------
    const orbs = {}; ORDER.forEach((n, i) => { orbs[n] = makeOrb(n, 97 + i * 131); });
    const flow = LINKS.map(() => 0), acc = LINKS.map(() => 0);
    let parts = [], rings = [], W = 0, H = 0, dpr = 1, wide = true, last = 0, raf = 0, dirty = true, dead = false;
    let cur = null;

    function resize() {
      const w = Math.max(280, Math.floor(host.clientWidth || 600));
      wide = w >= 640;
      const h = wide ? Math.min(600, Math.round(w * 0.54)) : Math.round(w * 1.22);
      dpr = Math.min(2, window.devicePixelRatio || 1);
      if (w === W && h === H) return;
      W = w; H = h;
      canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
      canvas.style.height = H + "px";
      const P = wide ? { QUANT: [0.19, 0.27], CHARTIST: [0.81, 0.27], SENTINEL: [0.19, 0.75], WARDEN: [0.81, 0.75], ARBITER: [0.5, 0.48] }
                     : { QUANT: [0.25, 0.17], CHARTIST: [0.75, 0.17], ARBITER: [0.5, 0.48], SENTINEL: [0.25, 0.80], WARDEN: [0.75, 0.80] };
      const base = wide ? H * 0.145 : W * 0.15;
      ORDER.forEach((n) => { const o = orbs[n]; o.x = P[n][0] * W; o.y = P[n][1] * H; o.R = n === "ARBITER" ? base * 1.5 : base; });
      dirty = true;
    }

    function curve(i, t) {
      const a = orbs[LINKS[i][0]], b = orbs[LINKS[i][1]], bend = LINKS[i][2];
      const dx = b.x - a.x, dy = b.y - a.y, len = Math.hypot(dx, dy) || 1;
      const ux = dx / len, uy = dy / len;
      const x0 = a.x + ux * a.R * 1.05, y0 = a.y + uy * a.R * 1.05, x1 = b.x - ux * b.R * 1.05, y1 = b.y - uy * b.R * 1.05;
      const cx = (x0 + x1) / 2 - uy * len * bend, cy = (y0 + y1) / 2 + ux * len * bend, m = 1 - t;
      return [m * m * x0 + 2 * m * t * cx + t * t * x1, m * m * y0 + 2 * m * t * cy + t * t * y1];
    }
    function burst(key, rgb, n) {
      const i = L[key]; if (i == null) return;
      for (let k = 0; k < n; k++) parts.push({ i: i, t: -k * 0.035, v: 0.55 + Math.random() * 0.25, rgb: rgb, s: 2.4 });
    }
    function shock(name, rgb) { const o = orbs[name]; rings.push({ x: o.x, y: o.y, r: o.R, v: o.R * 2.4, life: 1, rgb: rgb }); o.flare = 1; }

    function drawOrb(o, time, dt) {
      const c = o.rgb, a = o.act, R = o.R;
      o.spin += dt * (0.12 + a * 0.75);
      const glow = ctx.createRadialGradient(o.x, o.y, 0, o.x, o.y, R * 1.9);
      glow.addColorStop(0, rgba(c, 0.20 + a * 0.30 + o.flare * 0.3)); glow.addColorStop(0.45, rgba(c, 0.05 + a * 0.08)); glow.addColorStop(1, rgba(c, 0));
      ctx.fillStyle = glow; ctx.beginPath(); ctx.arc(o.x, o.y, R * 1.9, 0, 6.283); ctx.fill();
      const cs = Math.cos(o.spin), sn = Math.sin(o.spin);
      const bright = (o.dormant ? 0.16 : 0.34 + a * 0.66) + o.flare * 0.4;
      for (let ri = 0; ri < o.rings.length; ri++) {
        const g = o.rings[ri];
        if (o.dormant && ri % 3) continue;
        const ph = g.ph + time * g.spd * (0.35 + a * 1.6);
        const ci = Math.cos(g.inc), si = Math.sin(g.inc), cn = Math.cos(g.node), s2 = Math.sin(g.node), rr = g.rad * R;
        for (let ai = 0; ai < g.arcs.length; ai++) {
          const arc = g.arcs[ai], steps = Math.max(4, Math.round(arc[1] * 7));
          let zs = 0;
          ctx.beginPath();
          for (let s = 0; s <= steps; s++) {
            const ang = ph + arc[0] + arc[1] * s / steps, px = Math.cos(ang) * rr, py = Math.sin(ang) * rr;
            const y1 = py * ci, z1 = py * si;                 // tilt
            const x2 = px * cn - y1 * s2, y2 = px * s2 + y1 * cn;   // node
            const x3 = x2 * cs + z1 * sn, z3 = -x2 * sn + z1 * cs;  // spin about vertical
            zs += z3;
            if (s === 0) ctx.moveTo(o.x + x3, o.y + y2); else ctx.lineTo(o.x + x3, o.y + y2);
          }
          const depth = 0.5 + 0.5 * (zs / (steps + 1)) / rr;
          ctx.strokeStyle = rgba(c, bright * (0.22 + 0.78 * depth) * (arc[2] > 1 ? 0.55 : 0.9));
          ctx.lineWidth = arc[2] * (0.7 + depth * 0.7);
          ctx.stroke();
        }
      }
      for (let i = 0; i < o.sparks.length; i++) {
        const p = o.sparks[i], tw = 0.5 + 0.5 * Math.sin(time * (1.5 + a * 4) * p[4] + p[3]);
        if (o.dormant && i % 4) continue;
        const x = (p[0] * cs + p[2] * sn) * R, z = -p[0] * sn + p[2] * cs;
        ctx.fillStyle = rgba(c, bright * tw * (0.25 + 0.75 * (z * 0.5 + 0.5)));
        ctx.fillRect(o.x + x - 1, o.y + p[1] * R - 1, 2, 2);
      }
      // the eye at the centre
      const pulse = 1 + 0.08 * Math.sin(time * (1.2 + a * 5));
      ctx.strokeStyle = rgba(c, bright * 0.9); ctx.lineWidth = 1.2;
      ctx.beginPath(); ctx.arc(o.x, o.y, R * 0.16 * pulse, 0, 6.283); ctx.stroke();
      ctx.strokeStyle = rgba(c, bright * 0.5);
      ctx.beginPath(); ctx.arc(o.x, o.y, R * 0.27 * pulse, time * 0.6, time * 0.6 + 4.4); ctx.stroke();
      const core = ctx.createRadialGradient(o.x, o.y, 0, o.x, o.y, R * 0.2);
      core.addColorStop(0, rgba([255, 255, 255], 0.5 * bright)); core.addColorStop(1, rgba(c, 0));
      ctx.fillStyle = core; ctx.beginPath(); ctx.arc(o.x, o.y, R * 0.2, 0, 6.283); ctx.fill();
    }

    function draw(ts) {
      if (dead) return;
      raf = still ? 0 : requestAnimationFrame(draw);
      const time = ts / 1000, dt = Math.min(0.05, last ? time - last : 0.016); last = time;
      resize();
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.globalCompositeOperation = "source-over";
      const bg = ctx.createRadialGradient(W / 2, H * 0.48, 0, W / 2, H * 0.48, Math.max(W, H) * 0.75);
      bg.addColorStop(0, "#0b1322"); bg.addColorStop(1, "#05080f");
      ctx.fillStyle = bg; ctx.fillRect(0, 0, W, H);
      ctx.globalCompositeOperation = "lighter";

      // beams
      for (let i = 0; i < LINKS.length; i++) {
        const src = orbs[LINKS[i][0]];
        ctx.strokeStyle = rgba(src.rgb, 0.05 + flow[i] * 0.16); ctx.lineWidth = 1;
        ctx.beginPath();
        for (let s = 0; s <= 24; s++) { const p = curve(i, s / 24); if (s) ctx.lineTo(p[0], p[1]); else ctx.moveTo(p[0], p[1]); }
        ctx.stroke();
        if (!still) { acc[i] += flow[i] * 7 * dt; while (acc[i] >= 1) { acc[i] -= 1; parts.push({ i: i, t: 0, v: 0.3 + Math.random() * 0.2, rgb: src.rgb, s: 1.5 }); } }
      }
      const keep = [];
      for (let k = 0; k < parts.length; k++) {
        const p = parts[k]; p.t += p.v * dt;
        if (p.t >= 1) { orbs[LINKS[p.i][1]].flare = Math.min(1, orbs[LINKS[p.i][1]].flare + 0.05 * p.s); continue; }
        keep.push(p);
        if (p.t < 0) continue;
        const a = curve(p.i, p.t), b = curve(p.i, Math.max(0, p.t - 0.05));
        ctx.strokeStyle = rgba(p.rgb, 0.55); ctx.lineWidth = p.s;
        ctx.beginPath(); ctx.moveTo(b[0], b[1]); ctx.lineTo(a[0], a[1]); ctx.stroke();
        ctx.fillStyle = rgba([255, 255, 255], 0.75); ctx.fillRect(a[0] - 1, a[1] - 1, 2, 2);
      }
      parts = keep.length > 900 ? keep.slice(-900) : keep;
      rings = rings.filter((r) => {
        r.r += r.v * dt; r.life -= dt * 1.1;
        if (r.life <= 0) return false;
        ctx.strokeStyle = rgba(r.rgb, r.life * 0.7); ctx.lineWidth = 2;
        ctx.beginPath(); ctx.arc(r.x, r.y, r.r, 0, 6.283); ctx.stroke(); return true;
      });
      ORDER.forEach((n) => {
        const o = orbs[n];
        o.act += (o.target - o.act) * Math.min(1, dt * 2.5); o.flare = Math.max(0, o.flare - dt * 1.6);
        drawOrb(o, time, dt);
      });

      // labels
      ctx.globalCompositeOperation = "source-over"; ctx.textAlign = "center";
      ORDER.forEach((n) => {
        const o = orbs[n], y = o.y + o.R + (wide ? 20 : 16);
        ctx.font = "700 " + (wide ? 12 : 11) + "px ui-sans-serif, system-ui, sans-serif";
        ctx.fillStyle = rgba(o.rgb, 0.95); ctx.fillText(n.split("").join(" "), o.x, y);
        ctx.font = "600 " + (wide ? 12 : 11) + "px ui-sans-serif, system-ui, sans-serif";
        ctx.fillStyle = "rgba(230,238,248,0.92)"; ctx.fillText(o.label, o.x, y + 15);
      });
      dirty = false;
    }
    const kick = () => { if (still && !dead) requestAnimationFrame(draw); };

    // ---- data in ------------------------------------------------------
    const side = (v) => (v > 0.05 ? "LONG " : v < -0.05 ? "SHORT " : "");
    function push(f, quiet) {
      cur = f;
      const q = orbs.QUANT, c = orbs.CHARTIST, a = orbs.ARBITER, w = orbs.WARDEN, s = orbs.SENTINEL;
      q.target = Math.min(1, Math.abs(f.qv) * 1.8 + 0.08); c.target = Math.min(1, Math.abs(f.cv) * 1.8 + 0.08);
      a.target = Math.min(1, Math.abs(f.as) / ENTRY * 0.6 + (f.pos ? 0.35 : 0.05));
      w.target = f.wb ? 0.95 : 0.25 + (1 - f.wa) * 0.5;
      const hearing = !f.nl && f.nh > 0;     // feed connected, nothing market-moving right now
      s.dormant = !f.nl && !hearing; s.target = f.nl ? Math.min(1, Math.abs(f.nv) * 2 + 0.2) : hearing ? 0.16 : 0.05;
      q.label = side(f.qv) ? side(f.qv) + sgn(f.qv) : "NO VIEW";
      c.label = side(f.cv) ? side(f.cv) + sgn(f.cv) : "NO VIEW";
      a.label = f.pos ? "HOLDING " + (f.pos > 0 ? "LONG" : "SHORT") : Math.abs(f.as) >= ENTRY ? "WANTS " + (f.as > 0 ? "LONG" : "SHORT") : "WAITING";
      w.label = f.wb ? "BLOCKING" : "CLEAR";
      s.label = f.nl ? (side(f.nv) || "NEWS ") + sgn(f.nv) : hearing ? "LISTENING" : "NO FEED";
      flow[L["QUANT>CHARTIST"]] = Math.abs(f.qv) * 0.5;
      flow[L["QUANT>ARBITER"]] = Math.abs(f.qv); flow[L["CHARTIST>ARBITER"]] = Math.abs(f.cv);
      flow[L["SENTINEL>QUANT"]] = f.nl ? Math.abs(f.nv) : 0; flow[L["SENTINEL>ARBITER"]] = f.nl ? Math.abs(f.nv) : 0;
      flow[L["ARBITER>WARDEN"]] = !f.pos && Math.abs(f.as) >= ENTRY ? 0.8 : 0.04; flow[L["WARDEN>ARBITER"]] = f.wb ? 0.6 : 0;

      card.QUANT.state.textContent = q.label;
      card.QUANT.d1.textContent = "Trend " + sgn(f.qt, 1) + " · Momentum " + sgn(f.qr, 1) + " · Stretch " + sgn(f.qz, 1);
      card.QUANT.d2.textContent = "RSI " + f.qi + " · Confidence " + Math.round(f.qf * 100) + "%";
      card.CHARTIST.state.textContent = c.label;
      card.CHARTIST.d1.textContent = "Structure " + sgn(f.cs) + " · Break " + sgn(f.cb) + " · vs VWAP " + sgn(f.cw, 1);
      card.CHARTIST.d2.textContent = "Coiled " + Math.round(f.cc * 100) + "% · Confidence " + Math.round(f.cf * 100) + "%";
      card.ARBITER.state.textContent = a.label;
      card.ARBITER.d1.textContent = "Score " + sgn(f.as) + " · entry bar ±" + ENTRY;
      card.ARBITER.d2.textContent = "Agents agreeing " + f.ag + " · Vote split " + (+f.ad).toFixed(2);
      card.WARDEN.state.textContent = w.label;
      card.WARDEN.d1.textContent = f.wb ? "Why: " + f.wb : "No objection to a new trade";
      card.WARDEN.d2.textContent = "Risk appetite " + Math.round(f.wa * 100) + "%";
      card.SENTINEL.state.textContent = s.label;
      card.SENTINEL.d1.textContent = f.nl ? "News bias " + sgn(f.nv) + " \u00b7 " + f.nh + " headlines read"
        : hearing ? f.nh + " headlines read, none moving this market now" : "No news source connected";
      card.SENTINEL.d2.textContent = f.nx ? "Last that scored: " + f.nx : hearing ? "" : "The other four are working from price alone";

      hPx.textContent = "$" + (+f.px).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
      hTime.textContent = String(f.t).slice(5, 16).replace("T", " ") + " UTC";
      hPos.textContent = f.pos > 0 ? "Long from " + f.en : f.pos < 0 ? "Short from " + f.en : "None";
      hOpen.textContent = f.pos ? money(+f.up) : "—"; hOpen.className = "vm-v" + (f.pos ? (+f.up >= 0 ? " vm-up" : " vm-down") : "");
      hPnl.textContent = money(+f.pnl) + " · " + f.nt + " trades"; hPnl.className = "vm-v" + (+f.pnl > 0 ? " vm-up" : +f.pnl < 0 ? " vm-down" : "");

      const evs = typeof f.ev === "string" ? (f.ev ? f.ev.split("|").map((x) => x.split("~")) : []) : (f.ev || []);
      evs.forEach((e) => {
        const k = e[0], text = e[1];
        if (!quiet) {
          if (k === "break") burst("QUANT>CHARTIST", q.rgb, 9);
          else if (k === "counter") { burst("QUANT>CHARTIST", q.rgb, 5); c.flare = 0.6; }
          else if (k === "trap") shock("CHARTIST", c.rgb);
          else if (k === "open") { burst("ARBITER>WARDEN", a.rgb, 12); shock("ARBITER", a.rgb); shock("WARDEN", [90, 240, 160]); }
          else if (k === "veto") { burst("ARBITER>WARDEN", a.rgb, 5); burst("WARDEN>ARBITER", w.rgb, 10); shock("WARDEN", w.rgb); }
          else if (k === "wait") a.flare = 0.5;
          else if (k === "news") { burst("SENTINEL>QUANT", s.rgb, 8); burst("SENTINEL>ARBITER", s.rgb, 8); shock("SENTINEL", s.rgb); }
          else if (k === "win") shock("ARBITER", [90, 240, 160]);
          else if (k === "loss") shock("ARBITER", [255, 107, 107]);
        }
        if (empty.parentNode) empty.remove();
        const li = el("li", "vm-log-item");
        li.append(el("span", "vm-log-time", String(f.t).slice(11, 16)), el("span", "vm-log-text" + (k === "win" ? " vm-up" : k === "loss" ? " vm-down" : ""), text));
        list.prepend(li);
        while (list.children.length > 40) list.lastChild.remove();
      });
      dirty = true; kick();
    }
    function price(px, next) {
      if (px) hPx.textContent = "$" + (+px).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
      if (opts.mode === "live") hNext.textContent = next > 0 ? next + "s" : "now";
      if (cur && cur.pos && cur.sz && px) {
        const up = (px - cur.en) * cur.pos * cur.sz;
        hOpen.textContent = money(up); hOpen.className = "vm-v " + (up >= 0 ? "vm-up" : "vm-down");
      }
    }
    function setBadge(text, kind) { badge.textContent = text; badge.className = "vm-badge" + (kind ? " vm-badge-" + kind : ""); }
    setBadge(opts.mode === "live" ? "CONNECTING" : "REPLAY", opts.mode === "live" ? "" : "replay");

    // ---- replay -------------------------------------------------------
    let frames = null, at = 0, timer = 0, playing = false, speed = 4;
    function tickReplay() { if (!frames || !frames.length) return; push(frames[at]); at = (at + 1) % frames.length; }
    function setPlaying(on) {
      playing = on; clearInterval(timer); timer = 0;
      if (on) timer = setInterval(tickReplay, 1000 / speed);
      if (btnPlay) { btnPlay.textContent = on ? "Pause" : "Play"; btnPlay.setAttribute("aria-pressed", String(on)); }
    }
    let btnPlay = null;
    if (opts.mode !== "live") {
      btnPlay = el("button", "vm-btn", "Play"); btnPlay.type = "button"; btnPlay.id = "vm-play";
      btnPlay.addEventListener("click", () => setPlaying(!playing));
      const speeds = [1, 4, 12, 40];
      const btnSpeed = el("button", "vm-btn", ""); btnSpeed.type = "button"; btnSpeed.id = "vm-speed";
      const label = () => { hNext.textContent = speed + " bars a second"; btnSpeed.textContent = "Faster"; };
      btnSpeed.addEventListener("click", () => { speed = speeds[(speeds.indexOf(speed) + 1) % speeds.length]; label(); if (playing) setPlaying(true); });
      label(); ctl.append(btnPlay, btnSpeed);
    }
    function play(rows) {
      frames = rows; at = 0;
      if (!rows || !rows.length) return;
      // walk the first stretch silently so the readouts and log start populated
      const pre = Math.min(rows.length - 1, 240);
      for (; at < pre; at++) push(rows[at], true);
      setPlaying(!still);
    }

    const ro = window.ResizeObserver ? new ResizeObserver(() => { dirty = true; kick(); }) : null;
    if (ro) ro.observe(host);
    requestAnimationFrame(draw);

    return {
      push: push, price: price, play: play, badge: setBadge,
      destroy: function () { dead = true; cancelAnimationFrame(raf); clearInterval(timer); if (ro) ro.disconnect(); },
    };
  }
  window.VelocityMind = { mount: mount, agents: AG };
})();
