"""Renders a CortexSnapshot into a self-contained HTML HUD.

One template, two modes:
  - mode="static": data baked in as an inline JSON literal, zero network,
    zero external <script src> — safe for a strict-CSP artifact host.
  - mode="live":  adds a small JS poll of /api/snapshot.json so the local
    server can refresh the page in place.

Everything else — CSS, the canvas particle engine, the layout — is shared,
so the two delivery modes cannot visually drift apart.
"""
from __future__ import annotations

import dataclasses
import json
from typing import Literal

from .cortex import CortexSnapshot

Mode = Literal["live", "static"]

DISCLAIMER = (
    "Each region's core density and firing-rate number are driven by that "
    "agent's real logged data — the decision journal, sweep results, and "
    "watchlist. The soft outer drift is ambient motion for legibility, not a "
    "data signal. This is a visualization of committee activity, not a trained "
    "model and not a price prediction."
)


def render_html(snap: CortexSnapshot, *, mode: Mode = "static") -> str:
    data_json = json.dumps(dataclasses.asdict(snap))
    poll_js = _POLL_JS if mode == "live" else ""
    mode_badge = "LIVE" if mode == "live" else "SNAPSHOT"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>VANTRIX · Live Agent Cortex</title>
<style>{_CSS}</style>
</head>
<body>
<div id="app">
  <header>
    <div class="brand">VANTRIX &middot; LIVE AGENT CORTEX</div>
    <div class="mode-badge" data-mode="{mode}">{mode_badge}</div>
  </header>
  <main>
    <section id="stage">
      <canvas id="cortex"></canvas>
      <div id="nodes"></div>
    </section>
    <aside id="panel">
      <div class="panel-block" id="roster">
        <div class="panel-title">CORTEX <span id="live-count"></span></div>
        <div id="roster-rows"></div>
      </div>
      <div class="panel-block" id="readouts"></div>
      <div class="disclaimer">{DISCLAIMER}</div>
      <div class="generated">generated <span id="gen-at"></span></div>
    </aside>
  </main>
</div>
<script>
window.__CORTEX__ = {data_json};
{_APP_JS}
{poll_js}
</script>
</body>
</html>"""


_CSS = """
:root {
  --bg: #05070a; --panel: #0b0f16; --border: #1b2432;
  --text: #e5edf5; --dim: #6b7a8d; --live: #4ade80; --proxy: #fbbf24; --nodata: #64748b;
}
* { box-sizing: border-box; }
html, body { margin: 0; height: 100%; background: var(--bg); color: var(--text);
  font-family: "IBM Plex Mono", "SF Mono", ui-monospace, Menlo, Consolas, monospace; }
#app { display: flex; flex-direction: column; height: 100vh; }
header { display: flex; align-items: center; justify-content: space-between;
  padding: 14px 22px; border-bottom: 1px solid var(--border); flex: 0 0 auto; }
.brand { letter-spacing: 4px; font-size: 13px; color: #9fb3c8; font-weight: 600; }
.mode-badge { font-size: 10px; letter-spacing: 2px; padding: 3px 9px; border-radius: 3px;
  border: 1px solid var(--live); color: var(--live); }
.mode-badge[data-mode="static"] { border-color: var(--dim); color: var(--dim); }
main { display: flex; flex: 1 1 auto; min-height: 0; }
#stage { position: relative; flex: 1 1 auto; min-width: 0; overflow: hidden; }
#cortex { position: absolute; inset: 0; width: 100%; height: 100%; }
#nodes { position: absolute; inset: 0; pointer-events: none; }
.node { position: absolute; transform: translate(-50%, -50%); text-align: center;
  pointer-events: none; white-space: nowrap; }
.node .cn { font-size: 15px; font-weight: 600; letter-spacing: 2px; }
.node .role { font-size: 9px; letter-spacing: 2px; color: var(--dim);
  text-transform: uppercase; margin-top: 2px; }
.node .box { display: inline-flex; align-items: center; gap: 6px; margin-top: 6px;
  padding: 2px 8px; border: 1px solid var(--border); border-radius: 3px;
  background: rgba(5,7,10,0.7); font-size: 11px; }
.node .tag { font-size: 8px; letter-spacing: 1px; padding: 1px 4px; border-radius: 2px; }
.tag.live { color: var(--live); border: 1px solid rgba(74,222,128,0.4); }
.tag.proxy { color: var(--proxy); border: 1px solid rgba(251,191,36,0.4); }
.tag.no_data { color: var(--nodata); border: 1px solid rgba(100,116,139,0.4); }
#panel { flex: 0 0 340px; background: var(--panel); border-left: 1px solid var(--border);
  padding: 18px; overflow-y: auto; display: flex; flex-direction: column; gap: 16px; }
.panel-block { border: 1px solid var(--border); border-radius: 6px; padding: 12px 14px; }
.panel-title { font-size: 11px; letter-spacing: 3px; color: #9fb3c8; margin-bottom: 10px; }
.rrow { display: flex; align-items: center; gap: 8px; font-size: 12px; padding: 4px 0; }
.rrow .dot { width: 8px; height: 8px; border-radius: 50%; flex: 0 0 auto; }
.rrow .rcn { flex: 1 1 auto; letter-spacing: 1px; }
.rrow .rtag { font-size: 8px; letter-spacing: 1px; }
.rrow .rtag.live { color: var(--live); } .rrow .rtag.proxy { color: var(--proxy); }
.rrow .rtag.no_data { color: var(--nodata); }
.rrow .rval { font-variant-numeric: tabular-nums; color: var(--text); min-width: 56px;
  text-align: right; }
.readout { margin-bottom: 14px; }
.readout:last-child { margin-bottom: 0; }
.readout h4 { margin: 0 0 6px; font-size: 10px; letter-spacing: 2px; color: var(--dim);
  text-transform: uppercase; }
.readout .big { font-size: 22px; font-variant-numeric: tabular-nums; }
.readout .sub { font-size: 11px; color: var(--dim); }
.readout .pos { color: var(--live); } .readout .neg { color: #f87171; }
.readout .line { font-size: 11px; padding: 3px 0; border-top: 1px solid var(--border);
  display: flex; justify-content: space-between; gap: 10px; }
.readout .line:first-of-type { border-top: none; }
.readout .muted { color: var(--dim); font-size: 11px; }
.disclaimer { font-size: 10px; line-height: 1.5; color: var(--dim); }
.generated { font-size: 9px; color: #3b4656; letter-spacing: 1px; }
@media (max-width: 820px) {
  main { flex-direction: column; } #panel { flex-basis: auto; border-left: none;
    border-top: 1px solid var(--border); } #stage { min-height: 55vh; }
}
"""


# The rendering engine: layout, particle canvas, roster + readout panels.
_APP_JS = r"""
(function () {
  var RING_R = { center: 0, inner: 0.20, middle: 0.37, outer: 0.50 };
  // Angular placement (degrees, 0 = up, clockwise) per agent key.
  var ANGLES = {
    "cio": 0,
    "portfolio-manager": 90, "risk-manager": 210, "behavioral-coach": 330,
    "equity-analyst": 18, "quant-analyst": 90, "macro-strategist": 162,
    "special-situations": 234, "setup-scanner": 306,
    "valuation-analyst": 250, "red-team": 290
  };
  // Firing-rate normalization per agent key -> 0..1 for core particle density.
  function norm(key, v) {
    if (v === null || v === undefined) return 0;
    switch (key) {
      case "portfolio-manager": case "valuation-analyst":
        return Math.max(0, Math.min(1, (v + 50) / 100)); // -50..+50% -> 0..1
      case "risk-manager": case "behavioral-coach":
      case "quant-analyst": case "red-team":
        return Math.max(0, Math.min(1, v / 100));          // a percentage
      default:
        return Math.max(0, Math.min(1, v / 20));            // a raw count
    }
  }

  var canvas = document.getElementById("cortex");
  var ctx = canvas.getContext("2d");
  var nodesEl = document.getElementById("nodes");
  var W = 0, H = 0, DPR = Math.min(window.devicePixelRatio || 1, 2);
  var clusters = [];

  function positions() {
    var cx = W / 2, cy = H / 2, R = Math.min(W, H);
    var agents = window.__CORTEX__.agents;
    var out = {};
    for (var key in agents) {
      var a = agents[key];
      var ang = (ANGLES[key] - 90) * Math.PI / 180;
      var r = RING_R[a.ring] * R;
      out[key] = { x: cx + Math.cos(ang) * r, y: cy + Math.sin(ang) * r, a: a };
    }
    return out;
  }

  function rebuild() {
    var pos = positions();
    clusters = [];
    nodesEl.innerHTML = "";
    for (var key in pos) {
      var p = pos[key], a = p.a;
      var n = norm(key, a.metric_value);
      var isHub = a.ring === "center";
      var coreN = a.status === "no_data" ? 0 : Math.round(6 + n * (isHub ? 70 : 48));
      var haloN = isHub ? 26 : 16;
      var spread = (isHub ? 78 : 46) * (Math.min(W, H) / 900);
      var parts = [];
      for (var i = 0; i < coreN + haloN; i++) {
        var core = i < coreN;
        var rr = Math.sqrt(Math.random()) * spread * (core ? 0.8 : 1.25);
        var th = Math.random() * Math.PI * 2;
        parts.push({ bx: Math.cos(th) * rr, by: Math.sin(th) * rr,
          ph: Math.random() * Math.PI * 2,
          sp: (core ? 0.6 + n * 1.4 : 0.25) * (0.6 + Math.random() * 0.8),
          rad: core ? 1.1 + Math.random() * 1.4 : 0.7 + Math.random() * 0.8,
          core: core });
      }
      clusters.push({ key: key, x: p.x, y: p.y, color: a.color, parts: parts, n: n });

      var el = document.createElement("div");
      el.className = "node";
      el.style.left = p.x + "px"; el.style.top = (p.y - spread - 22) + "px";
      var tagcls = a.status;
      el.innerHTML =
        '<div class="cn" style="color:' + a.color + '">' + a.codename + '</div>' +
        '<div class="role">' + a.role + '</div>' +
        '<div class="box"><span>' + a.metric_display + '</span>' +
        '<span class="tag ' + tagcls + '">' + tagLabel(a.status) + '</span></div>';
      nodesEl.appendChild(el);
    }
  }

  function tagLabel(s) { return s === "live" ? "LIVE" : s === "proxy" ? "PROXY" : "NO SIGNAL"; }

  function edges() {
    // Org-chart tree: hub -> gates -> nearest research -> pre-decision.
    return [
      ["cio","portfolio-manager"],["cio","risk-manager"],["cio","behavioral-coach"],
      ["portfolio-manager","equity-analyst"],["portfolio-manager","quant-analyst"],
      ["risk-manager","macro-strategist"],["risk-manager","setup-scanner"],
      ["behavioral-coach","special-situations"],
      ["equity-analyst","valuation-analyst"],["quant-analyst","valuation-analyst"],
      ["special-situations","red-team"],["setup-scanner","red-team"]
    ];
  }

  function resize() {
    var stage = document.getElementById("stage");
    W = stage.clientWidth; H = stage.clientHeight;
    canvas.width = W * DPR; canvas.height = H * DPR;
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    rebuild();
  }

  var byKey = {};
  function draw(t) {
    ctx.clearRect(0, 0, W, H);
    for (var i = 0; i < clusters.length; i++) byKey[clusters[i].key] = clusters[i];
    // connecting lines
    var es = edges();
    ctx.lineWidth = 1;
    for (var e = 0; e < es.length; e++) {
      var A = byKey[es[e][0]], B = byKey[es[e][1]];
      if (!A || !B) continue;
      var pulse = 0.10 + 0.10 * (0.5 + 0.5 * Math.sin(t / 900 + e));
      ctx.strokeStyle = "rgba(120,150,180," + pulse + ")";
      ctx.beginPath(); ctx.moveTo(A.x, A.y); ctx.lineTo(B.x, B.y); ctx.stroke();
    }
    // particles
    for (var c = 0; c < clusters.length; c++) {
      var cl = clusters[c];
      for (var j = 0; j < cl.parts.length; j++) {
        var pt = cl.parts[j];
        var wob = pt.core ? 3 + cl.n * 5 : 4;
        var x = cl.x + pt.bx + Math.cos(t / 1000 * pt.sp + pt.ph) * wob;
        var y = cl.y + pt.by + Math.sin(t / 1000 * pt.sp + pt.ph * 1.7) * wob;
        var alpha = pt.core ? (0.55 + 0.4 * Math.sin(t / 600 * pt.sp + pt.ph)) : 0.10;
        ctx.globalAlpha = Math.max(0, alpha);
        ctx.fillStyle = cl.color;
        ctx.beginPath(); ctx.arc(x, y, pt.rad, 0, Math.PI * 2); ctx.fill();
      }
    }
    ctx.globalAlpha = 1;
    requestAnimationFrame(draw);
  }

  function fmtPct(v) { return (v >= 0 ? "+" : "") + v.toFixed(1) + "%"; }

  function renderPanel() {
    var d = window.__CORTEX__;
    var live = 0, total = 0;
    var rows = "";
    for (var key in d.agents) {
      var a = d.agents[key]; total++;
      if (a.status === "live") live++;
      rows +=
        '<div class="rrow"><span class="dot" style="background:' + a.color + '"></span>' +
        '<span class="rcn">' + a.codename + '</span>' +
        '<span class="rtag ' + a.status + '">' + tagLabel(a.status) + '</span>' +
        '<span class="rval">' + a.metric_display + '</span></div>';
    }
    document.getElementById("live-count").textContent = live + " / " + total + " LIVE";
    document.getElementById("roster-rows").innerHTML = rows;
    document.getElementById("gen-at").textContent = d.generated_at.replace("T", " ").slice(0, 19) + " UTC";
    document.getElementById("readouts").innerHTML = readouts(d);
  }

  function readouts(d) {
    var h = "";
    // portfolio vs SPY
    h += '<div class="readout"><h4>Portfolio vs SPY</h4>';
    if (d.portfolio) {
      var p = d.portfolio, cls = p.gap >= 0 ? "pos" : "neg";
      h += '<div class="big ' + cls + '">' + (p.gap >= 0 ? "+$" : "-$") +
           Math.abs(p.gap).toLocaleString(undefined, {maximumFractionDigits: 0}) + '</div>' +
           '<div class="sub">' + fmtPct(p.excess_pct) + ' vs benchmark &middot; as of ' + p.as_of + '</div>';
    } else {
      h += '<div class="muted">No contributions, equity snapshot, or SPY data yet. ' +
           'Log a deposit with <code>hf-bot portfolio contribute</code>.</div>';
    }
    h += '</div>';
    // open theses
    h += '<div class="readout"><h4>Open theses</h4>';
    if (d.open_theses && d.open_theses.length) {
      for (var i = 0; i < Math.min(d.open_theses.length, 6); i++) {
        var t = d.open_theses[i];
        h += '<div class="line"><span>' + t.symbol + ' &middot; ' + t.decision +
             ' <span class="muted">(' + t.conviction + ')</span></span></div>';
      }
    } else { h += '<div class="muted">No open theses. <code>hf-bot journal add</code>.</div>'; }
    h += '</div>';
    // latest sweep
    h += '<div class="readout"><h4>Latest sweep</h4>';
    if (d.latest_sweep) {
      var s = d.latest_sweep;
      h += '<div class="line"><span>' + s.strategy_key + '</span><span class="' +
           (s.hit_rate_pct >= 50 ? "pos" : "neg") + '">' + s.hit_rate_pct.toFixed(0) +
           '% hit &middot; ' + fmtPct(s.median_excess_pts) + '</span></div>';
    } else { h += '<div class="muted">No sweep run yet. <code>hf-bot sweep</code>.</div>'; }
    h += '</div>';
    // watchlist
    h += '<div class="readout"><h4>Watchlist</h4>';
    if (d.watchlist && d.watchlist.length) {
      for (var w = 0; w < Math.min(d.watchlist.length, 8); w++) {
        var x = d.watchlist[w];
        h += '<div class="line"><span>' + x.symbol + '</span>' +
             '<span class="muted">' + x.strategy_key + (x.live_enabled ? '' : ' &middot; off') +
             '</span></div>';
      }
    } else { h += '<div class="muted">Watchlist empty.</div>'; }
    h += '</div>';
    return h;
  }

  window.__CORTEX_APPLY__ = function () { rebuild(); renderPanel(); };
  window.addEventListener("resize", resize);
  resize(); renderPanel();
  requestAnimationFrame(draw);
})();
"""


_POLL_JS = r"""
(function () {
  var REFRESH_MS = (window.__CORTEX_REFRESH_MS__ || 15000);
  setInterval(function () {
    fetch("/api/snapshot.json", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (data) { window.__CORTEX__ = data; if (window.__CORTEX_APPLY__) window.__CORTEX_APPLY__(); })
      .catch(function () { /* keep last-known snapshot on a failed poll */ });
  }, REFRESH_MS);
})();
"""
