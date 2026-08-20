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
    cmd_js = _CMD_JS if mode == "live" else ""
    mode_badge = "LIVE" if mode == "live" else "SNAPSHOT"
    # A static snapshot has no server, so it cannot dispatch commands. Render
    # the bar disabled with an honest note rather than a dead input — and omit
    # the fetch-based handler entirely, keeping the static file self-contained.
    if mode == "live":
        cmdbar = (
            '<form id="cmdbar" autocomplete="off">'
            '<span class="prompt">&#9670; APEX</span>'
            '<input id="cmdinput" type="text" '
            'placeholder="command your committee — e.g.  review ASTS   ·   '
            'how are we tracking vs SPY?   (Enter to send)" />'
            '<button type="submit" id="cmdsend">SEND</button>'
            '<span id="cmdmsg"></span></form>'
        )
    else:
        cmdbar = (
            '<div id="cmdbar" class="static">'
            '<span class="prompt">&#9670; APEX</span>'
            '<input id="cmdinput" type="text" disabled '
            'placeholder="static snapshot — run `hf-bot dashboard` to speak to APEX" />'
            '<button type="button" id="cmdsend" disabled>OFFLINE</button></div>'
        )
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
  <div class="frame tl"></div><div class="frame tr"></div>
  <div class="frame bl"></div><div class="frame br"></div>
  <header>
    <div class="brand"><span class="reactor"></span>VANTRIX &middot; LIVE AGENT CORTEX</div>
    <div id="committee-status"></div>
    <div class="mode-badge" data-mode="{mode}">{mode_badge}</div>
  </header>
  <main>
    <section id="stage">
      <canvas id="cortex"></canvas>
      <div id="nodes"></div>
      <div id="scan"></div>
      {cmdbar}
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
{cmd_js}
{poll_js}
</script>
</body>
</html>"""


_CSS = """
:root {
  --bg: #080b12; --bg2: #0b111c; --panel: rgba(13,19,30,0.82);
  --card: rgba(20,28,42,0.55); --card-hi: rgba(30,41,59,0.5);
  --border: rgba(148,175,205,0.12); --border-hi: rgba(148,175,205,0.20);
  --text: #eaf1f8; --dim: #93a4ba; --faint: #5f7288;
  --live: #35d29a; --proxy: #e6b769; --nodata: #64788e;
  --cyan: #58c7e6; --cyan-dim: rgba(88,199,230,0.45); --gold: #e6b769;
  --danger: #f0687d; --neg: #f0687d;
  --r: 12px; --r-sm: 8px;
  --shadow: 0 6px 22px rgba(0,0,0,0.35);
}
* { box-sizing: border-box; }
html, body { margin: 0; height: 100%; background:
  radial-gradient(1100px 720px at 30% 0%, #101a2b 0%, var(--bg) 58%) fixed, var(--bg);
  color: var(--text); -webkit-font-smoothing: antialiased;
  font-family: "IBM Plex Mono", "SF Mono", ui-monospace, Menlo, Consolas, monospace; }
#app { display: flex; flex-direction: column; height: 100vh; position: relative; }
/* corner frame brackets — subtle chrome */
.frame { position: fixed; width: 20px; height: 20px; border: 1px solid var(--cyan);
  opacity: 0.22; z-index: 50; pointer-events: none; border-radius: 2px; }
.frame.tl { top: 12px; left: 12px; border-right: 0; border-bottom: 0; }
.frame.tr { top: 12px; right: 12px; border-left: 0; border-bottom: 0; }
.frame.bl { bottom: 12px; left: 12px; border-right: 0; border-top: 0; }
.frame.br { bottom: 12px; right: 12px; border-left: 0; border-top: 0; }
header { display: flex; align-items: center; justify-content: space-between;
  padding: 15px 26px; border-bottom: 1px solid var(--border); flex: 0 0 auto;
  background: rgba(10,15,24,0.5); backdrop-filter: blur(8px); }
.brand { letter-spacing: 3.5px; font-size: 13px; color: var(--text); font-weight: 600;
  display: flex; align-items: center; gap: 11px; }
.reactor { width: 11px; height: 11px; border-radius: 50%;
  background: radial-gradient(circle, #d6f7ff 0%, var(--cyan) 50%, transparent 74%);
  box-shadow: 0 0 10px var(--cyan); animation: rpulse 2.8s ease-in-out infinite; }
@keyframes rpulse { 0%,100% { transform: scale(0.9); opacity: 0.85; } 50% { transform: scale(1.08); opacity: 1; } }
.mode-badge { font-size: 9px; letter-spacing: 2px; padding: 4px 10px; border-radius: 999px;
  border: 1px solid var(--cyan-dim); color: var(--cyan); background: rgba(88,199,230,0.08);
  font-weight: 600; }
.mode-badge[data-mode="static"] { border-color: var(--border); color: var(--dim); background: none; }
#committee-status { flex: 1 1 auto; text-align: center; font-size: 10.5px;
  letter-spacing: 2.5px; color: var(--faint); }
#committee-status .on { color: var(--cyan); }
#committee-status .dotpulse { animation: dp 1.2s ease-in-out infinite; }
@keyframes dp { 0%,100% { opacity: 0.3; } 50% { opacity: 1; } }
main { display: flex; flex: 1 1 auto; min-height: 0; }
#stage { position: relative; flex: 1 1 auto; min-width: 0; overflow: hidden; }
#cortex { position: absolute; inset: 0; width: 100%; height: 100%; }
#nodes { position: absolute; inset: 0; pointer-events: none; }
/* slow scan sweep — very subtle */
#scan { position: absolute; inset: 0; pointer-events: none; z-index: 2;
  background: linear-gradient(180deg, transparent 0%, rgba(88,199,230,0.03) 50%, transparent 100%);
  height: 40%; animation: sweep 9s linear infinite; opacity: 0.7; }
@keyframes sweep { 0% { transform: translateY(-120%); } 100% { transform: translateY(360%); } }
/* command bar — clean pill */
#cmdbar { position: absolute; left: 50%; bottom: 24px; transform: translateX(-50%);
  z-index: 6; display: flex; align-items: center; gap: 12px; width: min(700px, 84%);
  padding: 11px 16px; border: 1px solid var(--border-hi); border-radius: 999px;
  background: rgba(12,18,28,0.9); box-shadow: var(--shadow); backdrop-filter: blur(10px); }
#cmdbar:focus-within { border-color: var(--cyan-dim); box-shadow: 0 0 0 3px rgba(88,199,230,0.10), var(--shadow); }
#cmdbar .prompt { color: var(--cyan); font-size: 11px; letter-spacing: 1.5px; flex: 0 0 auto; }
#cmdinput { flex: 1 1 auto; background: transparent; border: none; outline: none;
  color: var(--text); font-family: inherit; font-size: 13px; letter-spacing: 0.3px; }
#cmdinput::placeholder { color: var(--faint); }
#cmdsend { flex: 0 0 auto; background: var(--cyan); color: #05161c;
  border: none; border-radius: 999px; cursor: pointer; font-weight: 700;
  font-family: inherit; font-size: 10px; letter-spacing: 1.5px; padding: 7px 15px; transition: filter .15s; }
#cmdsend:hover { filter: brightness(1.12); }
#cmdmsg { position: absolute; left: 16px; bottom: 100%; margin-bottom: 10px; font-size: 11px;
  color: var(--cyan); letter-spacing: 0.5px; white-space: nowrap; opacity: 0; transition: opacity 0.3s; }
#cmdmsg.show { opacity: 1; }
#cmdmsg.err { color: var(--danger); }
#cmdbar.static { opacity: 0.55; }
#cmdbar.static #cmdinput { cursor: not-allowed; }
#cmdbar.static #cmdsend { color: var(--dim); background: var(--card-hi); cursor: not-allowed; }
.node { position: absolute; transform: translate(-50%, -50%); text-align: center;
  pointer-events: none; white-space: nowrap; }
.node .cn { font-size: 14px; font-weight: 700; letter-spacing: 1.5px;
  text-shadow: 0 0 14px currentColor; }
.node .role { font-size: 8.5px; letter-spacing: 1.5px; color: var(--faint);
  text-transform: uppercase; margin-top: 3px; }
.node .box { display: inline-flex; align-items: center; gap: 7px; margin-top: 7px;
  padding: 3px 10px; border: 1px solid var(--border-hi); border-radius: 999px;
  background: rgba(10,16,26,0.85); font-size: 11px; backdrop-filter: blur(4px); }
.node .tag { font-size: 8px; letter-spacing: 0.5px; padding: 1px 6px; border-radius: 999px; font-weight: 600; }
.tag.live { color: var(--live); background: rgba(53,210,154,0.12); }
.tag.proxy { color: var(--proxy); background: rgba(230,183,105,0.12); }
.tag.no_data { color: var(--nodata); background: rgba(100,120,142,0.10); }
#panel { flex: 0 0 350px; background: var(--panel); border-left: 1px solid var(--border);
  padding: 16px; overflow-y: auto; display: flex; flex-direction: column; gap: 14px;
  backdrop-filter: blur(6px); }
#panel::-webkit-scrollbar { width: 8px; }
#panel::-webkit-scrollbar-thumb { background: var(--border-hi); border-radius: 8px; }
.panel-block { border: 1px solid var(--border); border-radius: var(--r); padding: 15px 16px;
  position: relative; background: var(--card); box-shadow: var(--shadow); }
.panel-title { font-size: 10.5px; letter-spacing: 2.5px; color: var(--dim);
  margin-bottom: 12px; text-transform: uppercase; font-weight: 600; }
/* command deck */
.cmd { display: flex; align-items: center; gap: 8px; font-size: 11px; padding: 6px 0;
  border-top: 1px solid var(--border); }
.cmd:first-of-type { border-top: none; }
.cmd .csym { flex: 1 1 auto; letter-spacing: 0.5px; color: var(--text); }
.cmd .cst { font-size: 8px; letter-spacing: 0.5px; padding: 2px 7px; border-radius: 999px; flex: 0 0 auto; font-weight: 600; }
.cst.running { color: var(--cyan); background: rgba(88,199,230,0.12); animation: dp 1.2s ease-in-out infinite; }
.cst.done { color: var(--live); background: rgba(53,210,154,0.12); }
.cst.pending { color: var(--dim); background: rgba(147,164,186,0.10); }
.cst.failed, .cst.unavailable { color: var(--danger); background: rgba(240,104,125,0.12); }
/* APEX console — the conversation */
.turn { margin: 10px 0; border-top: 1px solid var(--border); padding-top: 10px; }
.turn:first-of-type { border-top: none; padding-top: 0; }
.turn .who { display: inline-block; min-width: 40px; font-size: 8px; letter-spacing: 1.5px;
  color: var(--faint); vertical-align: top; font-weight: 600; }
.turn .you { font-size: 11.5px; color: var(--text); margin-bottom: 7px; }
.turn .you .cst { margin-left: 6px; }
.turn .apex { font-size: 11.5px; color: var(--dim); line-height: 1.6;
  white-space: pre-wrap; border-left: 2px solid var(--cyan-dim); padding-left: 10px; }
.turn .apex.working { color: var(--cyan); }
.turn .apex.muted2 { color: var(--faint); border-left-color: var(--border); }
/* orders (execution bridge) */
.ord { margin: 9px 0; border-top: 1px solid var(--border); padding-top: 9px; }
.ord:first-of-type { border-top: none; padding-top: 0; }
.ordline { display: flex; align-items: center; gap: 8px; justify-content: space-between; }
.ord .osym { font-size: 11.5px; color: var(--text); letter-spacing: 0.5px; }
.cst.proposed { color: var(--proxy); background: rgba(230,183,105,0.12); }
.cst.placed, .cst.filled { color: var(--live); background: rgba(53,210,154,0.12); }
.ordbtns { display: flex; gap: 8px; margin-top: 8px; }
.order-btn { flex: 1 1 auto; font-family: inherit; font-size: 10px; letter-spacing: 1.5px;
  padding: 7px 0; border-radius: var(--r-sm); cursor: pointer; background: transparent; font-weight: 600; transition: background .15s; }
.order-btn.ok { color: var(--live); border: 1px solid rgba(53,210,154,0.5); }
.order-btn.ok:hover { background: rgba(53,210,154,0.14); }
.order-btn.no { color: var(--dim); border: 1px solid var(--border-hi); }
.order-btn.no:hover { background: rgba(240,104,125,0.12); color: var(--danger); border-color: rgba(240,104,125,0.4); }
.order-btn:disabled { opacity: 0.5; cursor: default; }
.study-btn { font-family: inherit; font-size: 9px; letter-spacing: 1px; margin-left: 8px;
  padding: 3px 9px; border-radius: 999px; cursor: pointer; background: rgba(53,210,154,0.08);
  color: var(--live); border: 1px solid rgba(53,210,154,0.4); vertical-align: middle; font-weight: 600; transition: background .15s; }
.study-btn:hover { background: rgba(53,210,154,0.18); }
.study-btn.cycle { float: right; }
.study-btn:disabled { opacity: 0.4; cursor: default; border-color: var(--border); color: var(--dim); background: none; }
.study-btn.kill { color: var(--danger); border-color: rgba(240,104,125,0.5); background: rgba(240,104,125,0.08); }
.study-btn.kill:hover { background: rgba(240,104,125,0.18); }
.killrow { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.killstate { font-size: 11px; letter-spacing: 0.5px; font-weight: 600; }
.killstate.armed { color: var(--live); }
.killstate.halted { color: var(--danger); }
.rrow { display: flex; align-items: center; gap: 9px; font-size: 12px; padding: 6px 0; }
.rrow .dot { width: 7px; height: 7px; border-radius: 50%; flex: 0 0 auto; box-shadow: 0 0 8px currentColor; }
.rrow .rcn { flex: 1 1 auto; letter-spacing: 0.5px; }
.rrow .rtag { font-size: 8px; letter-spacing: 0.5px; }
.rrow .rtag.live { color: var(--live); } .rrow .rtag.proxy { color: var(--proxy); }
.rrow .rtag.no_data { color: var(--nodata); }
.rrow .rval { font-variant-numeric: tabular-nums; color: var(--text); min-width: 56px;
  text-align: right; }
.readout { margin-bottom: 16px; }
.readout:last-child { margin-bottom: 0; }
.readout h4 { margin: 0 0 8px; font-size: 10px; letter-spacing: 1.5px; color: var(--dim);
  text-transform: uppercase; font-weight: 600; }
.readout .big { font-size: 24px; font-variant-numeric: tabular-nums; font-weight: 600; letter-spacing: 0.5px; }
.spark { width: 100%; height: 46px; display: block; margin: 10px 0 2px; }
.price { margin: 9px 0; border-top: 1px solid var(--border); padding-top: 8px; }
.price:first-of-type { border-top: none; padding-top: 0; }
.price .pl { display: flex; justify-content: space-between; align-items: baseline; font-size: 11.5px; }
.price .psym { letter-spacing: 0.5px; color: var(--text); }
.price .pval { font-variant-numeric: tabular-nums; }
.price .spark { height: 30px; margin: 4px 0 0; }
.readout .sub { font-size: 11px; color: var(--dim); }
.readout .pos { color: var(--live); } .readout .neg { color: var(--neg); }
.readout .line { font-size: 11.5px; padding: 5px 0; border-top: 1px solid var(--border);
  display: flex; justify-content: space-between; gap: 10px; }
.readout .line:first-of-type { border-top: none; }
.readout .muted { color: var(--dim); font-size: 11px; line-height: 1.5; }
.readout .ev { display: flex; gap: 8px; padding: 5px 0; font-size: 11px;
  border-top: 1px solid var(--border); }
.readout .ev:first-of-type { border-top: none; }
.readout .ev .evk { flex: 0 0 auto; color: var(--dim); letter-spacing: 0.5px;
  min-width: 108px; white-space: nowrap; }
.readout .ev .evt { flex: 1 1 auto; color: var(--faint); }
.disclaimer { font-size: 10px; line-height: 1.6; color: var(--faint); }
.generated { font-size: 9px; color: var(--faint); letter-spacing: 0.5px; opacity: 0.7; }
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
    "equity-analyst": 30, "quant-analyst": 90, "macro-strategist": 150,
    "special-situations": 210, "setup-scanner": 270, "sniper": 330,
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
      // Core particles = a baseline + the metric + logged ACTIVITY. The more an
      // agent has actually done (committee actions, studies), the denser its
      // cloud — so memory growth is visible as more dots by the agent's name.
      var metricN = a.status === "no_data" ? 0 : Math.round(n * (isHub ? 64 : 42));
      var actN = Math.min(isHub ? 60 : 44, (a.activity || 0) * 2);
      var coreN = Math.min(isHub ? 120 : 90, 3 + metricN + actN);
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

  function drawHud(t) {
    // Arc-reactor backdrop: concentric guide rings + a slow rotating reticle,
    // centered on APEX. Pure chrome — carries no data, just the Stark HUD feel.
    var hub = byKey["cio"];
    var cx = hub ? hub.x : W / 2, cy = hub ? hub.y : H / 2;
    var R = Math.min(W, H);
    ctx.save();
    ctx.strokeStyle = "rgba(95,230,255,0.06)"; ctx.lineWidth = 1;
    var rings = [0.20, 0.37, 0.50];
    for (var i = 0; i < rings.length; i++) {
      ctx.beginPath(); ctx.arc(cx, cy, rings[i] * R, 0, Math.PI * 2); ctx.stroke();
    }
    // rotating reticle: two arcs + tick marks
    var rr = 0.50 * R, rot = t / 6000;
    ctx.strokeStyle = "rgba(95,230,255,0.16)"; ctx.lineWidth = 1.4;
    ctx.beginPath(); ctx.arc(cx, cy, rr, rot, rot + 1.1); ctx.stroke();
    ctx.beginPath(); ctx.arc(cx, cy, rr, rot + Math.PI, rot + Math.PI + 1.1); ctx.stroke();
    ctx.strokeStyle = "rgba(255,207,112,0.14)";
    for (var k = 0; k < 12; k++) {
      var a = rot * 0.5 + k * Math.PI / 6;
      var r1 = 0.37 * R, r2 = 0.39 * R;
      ctx.beginPath();
      ctx.moveTo(cx + Math.cos(a) * r1, cy + Math.sin(a) * r1);
      ctx.lineTo(cx + Math.cos(a) * r2, cy + Math.sin(a) * r2);
      ctx.stroke();
    }
    ctx.restore();
  }

  var byKey = {};
  function draw(t) {
    ctx.clearRect(0, 0, W, H);
    for (var i = 0; i < clusters.length; i++) byKey[clusters[i].key] = clusters[i];
    drawHud(t);
    // connecting lines
    var es = edges();
    ctx.lineWidth = 1;
    for (var e = 0; e < es.length; e++) {
      var A = byKey[es[e][0]], B = byKey[es[e][1]];
      if (!A || !B) continue;
      var pulse = 0.10 + 0.12 * (0.5 + 0.5 * Math.sin(t / 900 + e));
      ctx.strokeStyle = "rgba(95,230,255," + pulse + ")";
      ctx.beginPath(); ctx.moveTo(A.x, A.y); ctx.lineTo(B.x, B.y); ctx.stroke();
    }
    // particles — the agent working RIGHT NOW flares bright (a live burst as
    // each agent acts during a run), so you see the committee firing in real time.
    var comm = window.__CORTEX__.committee;
    var activeKey = (comm && comm.state === "active") ? comm.active_agent : null;
    for (var c = 0; c < clusters.length; c++) {
      var cl = clusters[c];
      var hot = (cl.key === activeKey);
      // a fast burst envelope for the active agent (0.6..1.0), else steady
      var burst = hot ? (0.7 + 0.3 * Math.sin(t / 130)) : 1;
      for (var j = 0; j < cl.parts.length; j++) {
        var pt = cl.parts[j];
        var wob = pt.core ? 3 + cl.n * 5 : 4;
        var x = cl.x + pt.bx + Math.cos(t / 1000 * pt.sp + pt.ph) * wob;
        var y = cl.y + pt.by + Math.sin(t / 1000 * pt.sp + pt.ph * 1.7) * wob;
        var alpha = pt.core ? (0.55 + 0.4 * Math.sin(t / 600 * pt.sp + pt.ph)) : 0.10;
        var rad = pt.rad;
        if (hot && pt.core) { alpha = Math.min(1, alpha * 1.6) * burst + 0.15; rad = pt.rad * 1.35; }
        ctx.globalAlpha = Math.max(0, alpha);
        ctx.fillStyle = cl.color;
        if (hot && pt.core) { ctx.shadowColor = cl.color; ctx.shadowBlur = 7; }
        ctx.beginPath(); ctx.arc(x, y, rad, 0, Math.PI * 2); ctx.fill();
        if (hot && pt.core) ctx.shadowBlur = 0;
      }
    }
    ctx.globalAlpha = 1;
    drawActivity(t);
    requestAnimationFrame(draw);
  }

  // Handoff list for the current run: straight lines between the ACTUAL
  // source and target agent nodes, in event order. Each corresponds to a real
  // logged handoff — no handoff event, no pulse.
  function handoffs() {
    var c = window.__CORTEX__.committee;
    if (!c || !c.events) return [];
    var out = [];
    for (var i = 0; i < c.events.length; i++) {
      var e = c.events[i];
      if (e.event_type === "handoff" && e.to_agent) out.push([e.agent_key, e.to_agent]);
      else if (e.event_type === "memo") out.push([e.agent_key, "cio"]); // final convergence
    }
    return out;
  }

  function drawActivity(t) {
    var c = window.__CORTEX__.committee;
    if (!c || c.state === "idle") return;
    var hs = handoffs();
    if (!hs.length) return;

    // Timeline: one traveling pulse advances through the handoff sequence,
    // looping — this is the flow of work moving through the committee.
    var SLOT = 780;               // ms per handoff
    var GAP = 1400;               // pause before the sequence repeats
    var cycle = hs.length * SLOT + GAP;
    var pos = (t % cycle);
    var idx = Math.floor(pos / SLOT);
    var frac = (pos - idx * SLOT) / SLOT;

    // trace already-completed handoffs of this cycle as faint settled lines
    for (var k = 0; k < hs.length && k <= idx; k++) {
      var A = byKey[hs[k][0]], B = byKey[hs[k][1]];
      if (!A || !B) continue;
      ctx.strokeStyle = "rgba(125,211,252,0.18)";
      ctx.lineWidth = 1.2;
      ctx.beginPath(); ctx.moveTo(A.x, A.y); ctx.lineTo(B.x, B.y); ctx.stroke();
    }
    // the live pulse on the current handoff
    if (idx < hs.length) {
      var S = byKey[hs[idx][0]], T = byKey[hs[idx][1]];
      if (S && T) {
        var ease = frac < 0.5 ? 2 * frac * frac : 1 - Math.pow(-2 * frac + 2, 2) / 2;
        var px = S.x + (T.x - S.x) * ease, py = S.y + (T.y - S.y) * ease;
        // bright line from source fading to the pulse head
        var grad = ctx.createLinearGradient(S.x, S.y, T.x, T.y);
        grad.addColorStop(0, "rgba(125,211,252,0.05)");
        grad.addColorStop(Math.min(1, ease), "rgba(125,211,252,0.55)");
        grad.addColorStop(1, "rgba(125,211,252,0.0)");
        ctx.strokeStyle = grad; ctx.lineWidth = 1.6;
        ctx.beginPath(); ctx.moveTo(S.x, S.y); ctx.lineTo(T.x, T.y); ctx.stroke();
        // pulse head
        ctx.globalAlpha = 1; ctx.fillStyle = "#cdeffe";
        ctx.shadowColor = "#7dd3fc"; ctx.shadowBlur = 14;
        ctx.beginPath(); ctx.arc(px, py, 3.2, 0, Math.PI * 2); ctx.fill();
        ctx.shadowBlur = 0;
      }
    }
    // steady glow ring on whoever is working now (active runs only)
    if (c.state === "active" && c.active_agent && byKey[c.active_agent]) {
      var N = byKey[c.active_agent];
      var r = 30 + 6 * Math.sin(t / 220);
      ctx.strokeStyle = "rgba(125,211,252," + (0.35 + 0.2 * Math.sin(t / 220)) + ")";
      ctx.lineWidth = 2;
      ctx.beginPath(); ctx.arc(N.x, N.y, r, 0, Math.PI * 2); ctx.stroke();
    }
    // when concluded, pulse a halo on APEX — the final say converges there
    if (c.state === "complete" && byKey["cio"]) {
      var P = byKey["cio"];
      var rr = 34 + 8 * Math.sin(t / 500);
      ctx.strokeStyle = "rgba(125,211,252," + (0.18 + 0.12 * Math.sin(t / 500)) + ")";
      ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.arc(P.x, P.y, rr, 0, Math.PI * 2); ctx.stroke();
    }
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
    document.getElementById("committee-status").innerHTML = statusLine(d);
  }

  function statusLine(d) {
    var c = d.committee;
    if (!c || c.state === "idle") return "COMMITTEE IDLE";
    if (c.state === "active") {
      var who = c.active_agent ? codenameOf(c.active_agent) : "committee";
      return '<span class="on"><span class="dotpulse">●</span> REVIEWING ' +
             (c.symbol || '') + ' &middot; ' + who + ' WORKING</span>';
    }
    return '<span class="on">✓ REVIEW OF ' + (c.symbol || '') +
           ' CONCLUDED &middot; APEX ISSUED THE MEMO</span>';
  }

  var CODEBY = {};
  function codenameOf(key) {
    if (!CODEBY[key]) {
      var a = window.__CORTEX__.agents[key];
      CODEBY[key] = a ? a.codename : key;
    }
    return CODEBY[key];
  }

  function committeePanel(d) {
    var c = d.committee;
    var h = '<div class="readout"><h4>Committee activity</h4>';
    if (!c) {
      return h + '<div class="muted">No review has run yet. On your machine: ' +
             '<code>Use the cio agent to review &lt;TICKER&gt;</code>, or ' +
             '<code>hf-bot committee demo</code>.</div></div>';
    }
    var badge = c.state === "active" ? '<span class="pos">● WORKING</span>'
              : c.state === "complete" ? '<span class="pos">✓ CONCLUDED</span>'
              : '<span class="muted">○ IDLE</span>';
    h += '<div class="sub" style="margin-bottom:6px">' + badge +
         ' &middot; ' + (c.symbol || '—');
    if (c.state === "active" && c.active_agent) {
      h += ' &middot; <b style="color:' + d.agents[c.active_agent].color + '">' +
           codenameOf(c.active_agent) + '</b> working';
    }
    h += '</div>';
    // event ticker — last few actions, newest at the bottom
    var ev = c.events || [];
    for (var i = Math.max(0, ev.length - 6); i < ev.length; i++) {
      var e = ev[i];
      var who = codenameOf(e.agent_key);
      var arrow = (e.event_type === "handoff" && e.to_agent)
        ? ' → ' + codenameOf(e.to_agent) : '';
      h += '<div class="ev"><span class="evk">' + who + arrow + '</span>' +
           '<span class="evt">' + e.summary + '</span></div>';
    }
    return h + '</div>';
  }

  function memoryPanel(d) {
    var m = d.memory || {};
    var h = '<div class="readout"><h4>Collective memory</h4>';
    h += '<div class="line"><span class="muted">reviews run</span><span>' + (m.committee_runs || 0) + '</span></div>';
    h += '<div class="line"><span class="muted">decisions logged</span><span>' + (m.decisions_logged || 0) + '</span></div>';
    h += '<div class="line"><span class="muted">reviewed &amp; scored</span><span>' + (m.reviewed || 0) + '</span></div>';
    h += '<div class="line"><span class="muted">lessons captured</span><span>' + (m.lessons_captured || 0) + '</span></div>';
    if (m.discipline_pct !== null && m.discipline_pct !== undefined) {
      h += '<div class="line"><span class="muted">discipline</span><span class="' +
           (m.discipline_pct >= 80 ? 'pos' : 'neg') + '">' + m.discipline_pct.toFixed(0) + '%</span></div>';
    }
    // shared-memory episodes — the recall layer that carries across sessions
    var eps = m.recent_episodes || [];
    var mirrored = m.episodes_mirrored || 0, total = m.episodes_count || 0;
    h += '<div class="line" style="margin-top:4px"><span class="muted">shared-brain episodes</span>' +
         '<span>' + total + '</span></div>';
    for (var i = 0; i < eps.length; i++) {
      var e = eps[i];
      var mk = e.mirrored ? '<span class="pos" title="mirrored to Agently">◈</span>'
                          : '<span class="muted" title="local only — Agently not yet mirrored">◇</span>';
      h += '<div class="ev"><span class="evk">' + mk + ' ' + (e.symbol || e.kind) +
           '</span><span class="evt">' + e.title + '</span></div>';
    }
    var brainNote = total === 0
      ? 'No episodes yet. Each concluded review distills one, recalled at the start of the next.'
      : mirrored + ' of ' + total + ' mirrored to the Agently cross-session brain (◈); ' +
        'the rest are held locally (◇) until that brain is reachable.';
    h += '<div class="muted" style="margin-top:6px;font-size:10px">Grows with every ' +
         'review and outcome — an accumulating record that makes the team\'s ' +
         'calibration measurable, not a model that silently "gets smarter." ' +
         brainNote + '</div>';
    return h + '</div>';
  }

  function esc(s) {
    return (s == null ? "" : String(s)).replace(/[&<>]/g, function (ch) {
      return ch === "&" ? "&amp;" : ch === "<" ? "&lt;" : "&gt;";
    });
  }

  function apexConsolePanel(d) {
    var cmds = (d.commands || []).slice();   // newest-first from the server
    var live = !!window.__CORTEX_LIVE__;
    var archiveBtn = live
      ? '<button class="study-btn cycle" data-archive="1" title="Move older responses to research/committee/ files and keep the console clean">ARCHIVE</button>'
      : '';
    var h = '<div class="readout"><h4>APEX console' + archiveBtn + '</h4>';
    if (!cmds.length) {
      h += '<div class="muted">Speak to APEX from the bar below the cortex. He ' +
           'runs the committee and reports back here.</div>';
      return h + '</div>';
    }
    cmds.reverse();   // show oldest-first so the latest exchange sits at the bottom
    for (var i = 0; i < cmds.length; i++) {
      var c = cmds[i];
      var msg = c.message || (c.symbol ? "review " + c.symbol : "(command)");
      h += '<div class="turn">';
      h += '<div class="you"><span class="who">YOU</span>' + esc(msg) +
           '<span class="cst ' + c.status + '">' + c.status.toUpperCase() + '</span></div>';
      if (c.reply) {
        h += '<div class="apex"><span class="who">APEX</span>' + esc(c.reply) + '</div>';
      } else if (c.status === 'running') {
        h += '<div class="apex working"><span class="who">APEX</span>' +
             '<span class="dotpulse">●</span> working the committee…</div>';
      } else if (c.status === 'pending') {
        h += '<div class="apex muted2"><span class="who">APEX</span>' +
             'queued — awaiting an executor.</div>';
      } else if (c.detail) {
        h += '<div class="apex muted2"><span class="who">APEX</span>' + esc(c.detail) + '</div>';
      }
      h += '</div>';
    }
    return h + '</div>';
  }

  function knowledgePanel(d) {
    var k = d.knowledge || {};
    var pa = k.per_agent || {};
    var live = !!window.__CORTEX_LIVE__;
    var h = '<div class="readout"><h4>Knowledge / curriculum' +
      (live ? '<button class="study-btn cycle" data-cycle="1" title="Send the whole committee to study their next topics">STUDY CYCLE</button>' : '') +
      '</h4>';
    var total = k.absorbed_total || 0, cap = k.topic_total || 0;
    if (!cap) { return h + '<div class="muted">No curriculum loaded.</div></div>'; }
    h += '<div class="line"><span class="muted">library absorbed</span><span>' +
         total + ' / ' + cap + ' topics</span></div>';
    // per-agent coverage bars, in committee order — each with a Study button
    for (var key in d.agents) {
      var c = pa[key]; if (!c) continue;
      var a = c.absorbed, t = c.total, col = d.agents[key].color;
      var bar = '';
      for (var i = 0; i < t; i++) {
        bar += '<span style="color:' + (i < a ? col : '#2a3a4a') + '">&#9632;</span>';
      }
      var done = a >= t;
      var btn = live
        ? '<button class="study-btn" data-agent="' + key + '"' +
          (done ? ' disabled title="Curriculum complete"' : ' title="Research the next topic and add it to the library"') +
          '>' + (done ? 'DONE' : 'STUDY') + '</button>'
        : '';
      h += '<div class="ev"><span class="evk">' + codenameOf(key) + '</span>' +
           '<span class="evt" style="letter-spacing:1px">' + bar + '</span>' + btn + '</div>';
    }
    var rec = k.recent || [];
    if (rec.length) {
      h += '<div class="muted" style="margin-top:6px;font-size:10px">latest: ' +
           codenameOf(rec[0].agent) + ' &middot; ' + rec[0].topic + '</div>';
    }
    h += '<div id="studymsg" class="muted" style="margin-top:4px;font-size:10px;min-height:12px"></div>';
    h += '<div class="muted" style="margin-top:2px;font-size:10px">Each agent ' +
         'recalls its library at task time. STUDY sends it to research its next ' +
         'topic with its own tools (spends tokens) — a growing store of concepts ' +
         'and cases, not a retrained model.</div>';
    return h + '</div>';
  }

  function ordersPanel(d) {
    var orders = d.orders || [];
    var h = '<div class="readout"><h4>Orders &middot; paper</h4>';
    if (!orders.length) {
      return h + '<div class="muted">No orders staged. When the committee decides, ' +
             'VECTOR stages a proposal here for you to approve.</div></div>';
    }
    var live = !!window.__CORTEX_LIVE__;
    for (var i = 0; i < orders.length; i++) {
      var o = orders[i];
      var stop = o.stop_price ? ' &middot; stop $' + Number(o.stop_price).toFixed(2) : '';
      h += '<div class="ord"><div class="ordline">' +
           '<span class="osym">' + o.side.toUpperCase() + ' ' + Number(o.qty).toFixed(4) +
           ' ' + o.symbol + ' <span class="muted">~$' +
           Number(o.est_notional).toLocaleString(undefined,{maximumFractionDigits:0}) + stop +
           '</span></span>' +
           '<span class="cst ' + o.status + '">' + o.status.toUpperCase() + '</span></div>';
      if (o.status === 'proposed' && live) {
        h += '<div class="ordbtns">' +
             '<button class="order-btn ok" data-id="' + o.id + '" data-action="approve">APPROVE</button>' +
             '<button class="order-btn no" data-id="' + o.id + '" data-action="reject">REJECT</button>' +
             '</div>';
      } else if (o.detail && (o.status === 'failed' || o.status === 'rejected')) {
        h += '<div class="muted" style="font-size:10px">' + esc(o.detail) + '</div>';
      }
      h += '</div>';
    }
    return h + '</div>';
  }

  function sparkline(vals, w, hgt) {
    if (!vals || vals.length < 2) return "";
    var lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals);
    var span = (hi - lo) || 1, n = vals.length;
    var pts = vals.map(function (v, i) {
      var x = (i / (n - 1)) * w;
      var y = hgt - ((v - lo) / span) * (hgt - 4) - 2;
      return x.toFixed(1) + "," + y.toFixed(1);
    });
    var up = vals[n - 1] >= vals[0];
    var col = up ? "var(--live)" : "var(--danger)";
    var area = "0," + hgt + " " + pts.join(" ") + " " + w + "," + hgt;
    return '<svg class="spark" viewBox="0 0 ' + w + ' ' + hgt + '" preserveAspectRatio="none">' +
           '<polygon points="' + area + '" fill="' + col + '" fill-opacity="0.10"/>' +
           '<polyline points="' + pts.join(" ") + '" fill="none" stroke="' + col +
           '" stroke-width="1.5"/>' +
           '<circle cx="' + w + '" cy="' + pts[n-1].split(",")[1] + '" r="2.5" fill="' + col + '"/>' +
           '</svg>';
  }

  function systemPanel(d) {
    var sys = d.system || {};
    var live = !!window.__CORTEX_LIVE__;
    var on = !!sys.kill_switch;   // on == trading halted
    var h = '<div class="readout"><h4>System</h4>';
    h += '<div class="killrow">';
    h += '<span class="killstate ' + (on ? 'halted' : 'armed') + '">' +
         (on ? '● KILL SWITCH ON — halted' : '● TRADING ENABLED') + '</span>';
    if (live) {
      h += '<button class="study-btn' + (on ? '' : ' kill') + '" data-kill="' +
           (on ? 'off' : 'on') + '">' + (on ? 'ENABLE TRADING' : 'HALT') + '</button>';
    }
    h += '</div>';
    h += '<div class="muted" style="margin-top:6px">' +
         (on ? 'Order placement is blocked. Enable to approve staged orders (paper money).'
             : 'Staged orders can be approved — paper money only.') + '</div>';
    return h + '</div>';
  }

  function accountPanel(d) {
    var a = d.account;
    var h = '<div class="readout"><h4>Account &middot; paper</h4>';
    if (!a) {
      return h + '<div class="muted">No balance recorded yet. Run ' +
             '<code>hf-bot account</code> to pull it from the broker.</div></div>';
    }
    var cls = a.change_pct >= 0 ? "pos" : "neg";
    h += '<div class="big">$' + Number(a.equity).toLocaleString(undefined,{maximumFractionDigits:2}) + '</div>';
    h += '<div class="sub">equity &middot; <span class="' + cls + '">' + fmtPct(a.change_pct) +
         '</span> over ' + (a.history ? a.history.length : 0) + ' snapshots</div>';
    h += sparkline((a.history || []).map(function (p) { return p.equity; }), 300, 46);
    h += '<div class="line" style="margin-top:6px"><span class="muted">cash</span><span>$' +
         Number(a.cash).toLocaleString(undefined,{maximumFractionDigits:2}) + '</span></div>';
    h += '<div class="line"><span class="muted">as of</span><span>' + (a.as_of||'').slice(0,16).replace('T',' ') + '</span></div>';
    return h + '</div>';
  }

  function pricesPanel(d) {
    var prices = d.prices;
    var h = '<div class="readout"><h4>Prices &middot; live</h4>';
    if (!prices || !Object.keys(prices).length) {
      return h + '<div class="muted">Live prices load from Alpaca a moment after ' +
             'the server starts (watchlist + positions). Add symbols with ' +
             '<code>hf-bot watchlist</code>.</div></div>';
    }
    var syms = Object.keys(prices).sort();
    for (var i = 0; i < syms.length; i++) {
      var s = syms[i], p = prices[s];
      var cls = p.change_pct >= 0 ? "pos" : "neg";
      h += '<div class="price"><div class="pl">' +
           '<span class="psym">' + s + '</span>' +
           '<span class="pval">$' + Number(p.last).toLocaleString(undefined,{maximumFractionDigits:2}) +
           ' <span class="' + cls + '">' + fmtPct(p.change_pct) + '</span></span></div>' +
           sparkline(p.closes, 300, 30) + '</div>';
    }
    return h + '</div>';
  }

  function readouts(d) {
    var h = systemPanel(d) + accountPanel(d) + pricesPanel(d) + apexConsolePanel(d) + ordersPanel(d) + committeePanel(d) + memoryPanel(d) + knowledgePanel(d);
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


# Live-mode only: the command bar POSTs to /api/command. Omitted from static
# snapshots so an exported file contains no fetch() and stays self-contained.
_CMD_JS = r"""
(function () {
  window.__CORTEX_LIVE__ = true;   // enables order Approve/Reject buttons in the panel

  // Approve / reject a staged order — a direct, token-free action.
  document.addEventListener("click", function (ev) {
    var btn = ev.target.closest ? ev.target.closest(".order-btn") : null;
    if (!btn || btn.disabled) return;
    var id = btn.getAttribute("data-id"), action = btn.getAttribute("data-action");
    if (action === "approve" && !window.confirm("Place this PAPER order now?")) return;
    var row = btn.parentNode;
    row.innerHTML = '<span class="muted">' + (action === "approve" ? "placing…" : "rejecting…") + '</span>';
    fetch("/api/order", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({action: action, id: Number(id)})
    }).then(function (r) { return r.json(); })
      .then(function (res) {
        row.innerHTML = '<span class="' + (res.ok ? "" : "muted") + '" style="font-size:10px">' +
          (res.ok ? (res.status || "done").toUpperCase() + (res.order_id ? " · " + res.order_id : "")
                  : (res.error || "refused")) + '</span>';
        if (window.__CORTEX_REFRESH__) window.__CORTEX_REFRESH__();
      })
      .catch(function () { row.innerHTML = '<span class="muted">no server</span>'; });
  });

  // Study buttons — dispatch an agent (or the whole committee) to study its
  // next curriculum topic. A real agent run, so it goes through the executor.
  document.addEventListener("click", function (ev) {
    var btn = ev.target.closest ? ev.target.closest(".study-btn") : null;
    if (!btn || btn.disabled) return;
    // Kill switch toggle — halt/enable order placement.
    if (btn.getAttribute("data-kill")) {
      var wantOn = btn.getAttribute("data-kill") === "on";
      if (wantOn && !window.confirm("Turn the KILL SWITCH ON? This halts all order placement.")) return;
      btn.disabled = true;
      fetch("/api/kill-switch", {method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({active: wantOn})})
        .then(function (r) { return r.json(); })
        .then(function () {
          if (window.__CORTEX_REFRESH__) window.__CORTEX_REFRESH__();
          setTimeout(function () { btn.disabled = false; }, 1200);
        })
        .catch(function () { btn.disabled = false; });
      return;
    }
    // Archive button — clean the console, no tokens, no confirm needed.
    if (btn.getAttribute("data-archive")) {
      btn.disabled = true;
      fetch("/api/archive", {method: "POST", headers: {"Content-Type": "application/json"}, body: "{}"})
        .then(function (r) { return r.json(); })
        .then(function (j) {
          if (window.__CORTEX_REFRESH__) window.__CORTEX_REFRESH__();
          setTimeout(function () { btn.disabled = false; }, 1500);
        })
        .catch(function () { btn.disabled = false; });
      return;
    }
    var msg = document.getElementById("studymsg");
    var body = btn.getAttribute("data-cycle")
      ? {cycle: true}
      : {agent: btn.getAttribute("data-agent")};
    var who = body.cycle ? "the committee" : btn.getAttribute("data-agent");
    if (!window.confirm("Send " + who + " to study now? This runs an agent and spends tokens.")) return;
    if (msg) { msg.className = "show"; msg.textContent = "dispatching " + who + "…"; }
    btn.disabled = true;
    fetch("/api/study", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify(body)
    }).then(function (r) { return r.json().then(function (j) { return {ok: r.ok, j: j}; }); })
      .then(function (res) {
        if (msg) {
          msg.className = res.ok ? "show" : "show err";
          msg.textContent = res.ok ? (res.j.message || "dispatched") : (res.j.error || "rejected");
        }
        if (res.ok && window.__CORTEX_REFRESH__) window.__CORTEX_REFRESH__();
        setTimeout(function () { btn.disabled = false; }, 3000);
      })
      .catch(function () {
        if (msg) { msg.className = "show err"; msg.textContent = "no live server — run `hf-bot dashboard`"; }
        btn.disabled = false;
      });
    setTimeout(function () { if (msg) msg.className = ""; }, 8000);
  });

  var cmdbar = document.getElementById("cmdbar");
  if (!cmdbar || cmdbar.tagName !== "FORM") return;
  cmdbar.addEventListener("submit", function (ev) {
    ev.preventDefault();
    var input = document.getElementById("cmdinput");
    var msg = document.getElementById("cmdmsg");
    var text = (input.value || "").trim();
    if (!text) return;
    msg.className = "show"; msg.textContent = "dispatching…";
    fetch("/api/command", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({text: text})
    }).then(function (r) { return r.json().then(function (j) { return {ok: r.ok, j: j}; }); })
      .then(function (res) {
        if (!res.ok) { msg.className = "show err"; msg.textContent = res.j.error || "command rejected"; return; }
        msg.className = "show"; msg.textContent = res.j.message || ("queued " + res.j.symbol);
        input.value = "";
        if (window.__CORTEX_REFRESH__) window.__CORTEX_REFRESH__();
      })
      .catch(function () {
        msg.className = "show err";
        msg.textContent = "no live server — run `hf-bot dashboard` to dispatch commands";
      });
    setTimeout(function () { msg.className = ""; }, 6000);
  });
})();
"""


_POLL_JS = r"""
(function () {
  var REFRESH_MS = (window.__CORTEX_REFRESH_MS__ || 15000);
  function poll() {
    return fetch("/api/snapshot.json", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (data) { window.__CORTEX__ = data; if (window.__CORTEX_APPLY__) window.__CORTEX_APPLY__(); })
      .catch(function () { /* keep last-known snapshot on a failed poll */ });
  }
  // let the command bar force an immediate refresh after dispatching, then
  // poll a few extra times so a fast committee run shows up without waiting.
  window.__CORTEX_REFRESH__ = function () {
    poll();
    var n = 0, quick = setInterval(function () { poll(); if (++n >= 4) clearInterval(quick); }, 2500);
  };
  setInterval(poll, REFRESH_MS);
})();
"""
