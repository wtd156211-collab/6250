"""Self-contained HTML timeline report.

The page embeds the raw traces and renders one canvas section per
script with plain Canvas 2D calls: no fetch, no CDN, no services, and
byte-identical output across runs.
"""

import json
from pathlib import Path

from .runner import load_script, run_script

SCRIPTS_DIR = Path("samples/scripts")


def collect_sections(scripts_dir=SCRIPTS_DIR):
    """Run every script in scripts_dir (file-name order); return [(name, trace)]."""
    sections = []
    for path in sorted(Path(scripts_dir).glob("*.json"), key=lambda p: p.name):
        script = load_script(path)
        sections.append((script.get("name", path.stem), run_script(script)))
    return sections


def render_html(sections):
    payload = json.dumps(
        [{"name": name, "trace": trace} for name, trace in sections],
        ensure_ascii=True,
        separators=(",", ":"),
    ).replace("<", "\\u003c")
    if "__TRACE_DATA__" not in _TEMPLATE:
        raise RuntimeError("template placeholder missing")
    return _TEMPLATE.replace("__TRACE_DATA__", payload, 1)


_TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>SnapshotDB 并发时间线</title>
<style>
body { font-family: -apple-system, "Segoe UI", "Helvetica Neue", Arial, sans-serif;
       margin: 24px; color: #1f2937; background: #ffffff; }
h1 { font-size: 20px; margin: 0 0 4px; }
h2 { font-size: 15px; margin: 0 0 8px; }
section { margin-bottom: 36px; }
canvas { border: 1px solid #e5e7eb; border-radius: 6px; background: #ffffff;
         display: block; max-width: none; }
pre { background: #f8fafc; border: 1px solid #e5e7eb; border-radius: 6px;
      padding: 8px 10px; font-size: 11px; line-height: 1.55; overflow-x: auto;
      margin: 8px 0 0; }
.legend { font-size: 12px; color: #4b5563; margin: 0 0 16px; line-height: 1.7; }
.sw { display: inline-block; width: 9px; height: 9px; border-radius: 2px;
      margin: 0 4px 0 12px; vertical-align: -1px; }
.scroll { overflow-x: auto; padding-bottom: 4px; }
</style>
</head>
<body>
<h1>SnapshotDB 并发时间线</h1>
<p class="legend">横轴为轨迹中的时刻 t；每个事务一行。标记含义：
<span class="sw" style="background:#0d9488"></span>BEGIN
<span class="sw" style="background:#2563eb"></span>READ（键@版本，空读为 null，读自己的写为 -）
<span class="sw" style="background:#ea580c"></span>WRITE（键=值）
<span class="sw" style="background:#16a34a"></span>COMMIT（ver）
<span class="sw" style="background:#dc2626"></span>ABORT（reason）
<span class="sw" style="background:#6b7280"></span>ROLLBACK。
红色虚线从 ABORT 指向 with= 事务，线上标触发键与 reason。所有数字与判定均取自轨迹。</p>
<div id="app"></div>
<script id="trace-data" type="application/json">__TRACE_DATA__</script>
<script>
"use strict";
const SECTIONS = JSON.parse(document.getElementById("trace-data").textContent);

const COLORS = {
  BEGIN: "#0d9488", READ: "#2563eb", WRITE: "#ea580c",
  COMMIT: "#16a34a", ABORT: "#dc2626", ROLLBACK: "#6b7280"
};

function parseTrace(text) {
  const events = [], reclaims = [];
  let summary = null;
  for (const line of text.split("\\n")) {
    if (!line) continue;
    const p = line.split("|");
    const rec = { t: +p[0], ev: p[1], tx: p[2], key: p[3], ver: p[4], val: p[5], detail: p[6] };
    if (rec.ev === "SUMMARY") summary = rec;
    else if (rec.ev === "RECLAIM") reclaims.push(rec);
    else events.push(rec);
  }
  return { events: events, reclaims: reclaims, summary: summary };
}

function labelOf(e) {
  if (e.ev === "BEGIN") return "BEGIN " + e.detail;
  if (e.ev === "READ") return "READ " + e.key + "@" + (e.val === "null" ? "null" : e.ver);
  if (e.ev === "WRITE") return "WRITE " + e.key + "=" + e.val;
  if (e.ev === "COMMIT") return "COMMIT " + e.detail.replace(/ versions=\\d+$/, "");
  if (e.ev === "ABORT") return "ABORT " + /reason=\\S+/.exec(e.detail)[0];
  if (e.ev === "ROLLBACK") return "ROLLBACK " + /reason=\\S+/.exec(e.detail)[0];
  return e.ev;
}

function marker(ctx, kind, x, y) {
  ctx.fillStyle = COLORS[kind];
  ctx.strokeStyle = COLORS[kind];
  ctx.beginPath();
  if (kind === "BEGIN" || kind === "COMMIT") {
    ctx.arc(x, y, kind === "COMMIT" ? 5 : 4, 0, Math.PI * 2);
    ctx.fill();
  } else if (kind === "READ") {
    ctx.fillRect(x - 3.5, y - 3.5, 7, 7);
  } else if (kind === "WRITE") {
    ctx.moveTo(x, y - 5); ctx.lineTo(x + 5, y);
    ctx.lineTo(x, y + 5); ctx.lineTo(x - 5, y);
    ctx.closePath(); ctx.fill();
  } else {
    ctx.lineWidth = 2;
    ctx.moveTo(x - 4, y - 4); ctx.lineTo(x + 4, y + 4);
    ctx.moveTo(x + 4, y - 4); ctx.lineTo(x - 4, y + 4);
    ctx.stroke();
    ctx.lineWidth = 1;
  }
}

function render(sec) {
  const parsed = parseTrace(sec.trace);
  const events = parsed.events;

  const txOrder = [], seen = new Set();
  for (const e of events) {
    if (!seen.has(e.tx)) { seen.add(e.tx); txOrder.push(e.tx); }
  }
  const txIndex = new Map(txOrder.map((tx, i) => [tx, i]));
  const ticks = [...new Set(events.map(e => e.t))].sort((a, b) => a - b);
  const tickIndex = new Map(ticks.map((t, i) => [t, i]));

  const cells = new Map();
  for (const e of events) {
    const k = txIndex.get(e.tx) + "|" + e.t;
    let a = cells.get(k);
    if (!a) cells.set(k, a = []);
    a.push(e);
  }
  const rowMax = txOrder.map(() => 1);
  for (const a of cells.values()) {
    const i = txIndex.get(a[0].tx);
    if (a.length > rowMax[i]) rowMax[i] = a.length;
  }
  const rowH = rowMax.map(m => Math.max(40, m * 14 + 24));

  const leftPad = 100, topPad = 34, colW = 150, rightPad = 24, bottomPad = 16;
  const rowTop = [];
  let acc = topPad;
  for (const h of rowH) { rowTop.push(acc); acc += h; }
  const xOf = t => leftPad + tickIndex.get(t) * colW + colW / 2;
  const yMid = i => rowTop[i] + rowH[i] / 2;

  let W = leftPad + ticks.length * colW + rightPad;
  for (const e of events) {
    const need = xOf(e.t) + 12 + labelOf(e).length * 6.2 + 16;
    if (need > W) W = Math.ceil(need);
  }
  for (const e of events) {
    if (e.ev === "ABORT") { W = Math.max(W, xOf(e.t) + 170); }
  }
  const H = acc + bottomPad;

  const canvas = document.createElement("canvas");
  const dpr = window.devicePixelRatio || 1;
  canvas.width = W * dpr;
  canvas.height = H * dpr;
  canvas.style.width = W + "px";
  canvas.style.height = H + "px";
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  ctx.font = "10px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace";

  ctx.textAlign = "center";
  for (const t of ticks) {
    const x = xOf(t);
    ctx.strokeStyle = "#e5e7eb";
    ctx.beginPath(); ctx.moveTo(x, topPad - 8); ctx.lineTo(x, H - bottomPad); ctx.stroke();
    ctx.fillStyle = "#6b7280";
    ctx.fillText("t=" + t, x, topPad - 14);
  }
  txOrder.forEach((tx, i) => {
    ctx.strokeStyle = "#f1f5f9";
    ctx.beginPath(); ctx.moveTo(leftPad - 8, rowTop[i]); ctx.lineTo(W - 8, rowTop[i]); ctx.stroke();
    ctx.fillStyle = "#111827";
    ctx.textAlign = "right";
    ctx.fillText(tx, leftPad - 14, yMid(i) + 3);
    ctx.textAlign = "center";
  });

  const span = new Map();
  for (const e of events) {
    let s = span.get(e.tx);
    if (!s) span.set(e.tx, s = [e.t, e.t]);
    if (e.t < s[0]) s[0] = e.t;
    if (e.t > s[1]) s[1] = e.t;
  }
  ctx.strokeStyle = "#cbd5e1";
  for (const [tx, s] of span) {
    const y = yMid(txIndex.get(tx));
    ctx.beginPath(); ctx.moveTo(xOf(s[0]), y); ctx.lineTo(xOf(s[1]), y); ctx.stroke();
  }

  for (const e of events) {
    if (e.ev !== "ABORT") continue;
    const mw = /with=(\\S+)/.exec(e.detail);
    const mr = /reason=(\\S+)/.exec(e.detail);
    if (!mw || !mr || !txIndex.has(mw[1])) continue;
    const x = xOf(e.t);
    const y1 = yMid(txIndex.get(e.tx));
    const y2 = yMid(txIndex.get(mw[1]));
    const bend = x + 80;
    ctx.strokeStyle = COLORS.ABORT;
    ctx.setLineDash([5, 3]);
    ctx.beginPath();
    ctx.moveTo(x, y1);
    ctx.quadraticCurveTo(bend, (y1 + y2) / 2, x, y2);
    ctx.stroke();
    ctx.setLineDash([]);
    const dir = y2 >= y1 ? 1 : -1;
    ctx.fillStyle = COLORS.ABORT;
    ctx.beginPath();
    ctx.moveTo(x, y2);
    ctx.lineTo(x - 5, y2 - 8 * dir);
    ctx.lineTo(x + 5, y2 - 8 * dir);
    ctx.closePath(); ctx.fill();
    const label = e.key + " " + mr[1];
    const lx = bend - 6, ly = (y1 + y2) / 2 - 4;
    const wdt = ctx.measureText(label).width;
    ctx.fillStyle = "rgba(255,255,255,0.92)";
    ctx.fillRect(lx - wdt / 2 - 3, ly - 9, wdt + 6, 13);
    ctx.fillStyle = COLORS.ABORT;
    ctx.fillText(label, lx, ly + 1);
  }

  ctx.textAlign = "left";
  for (const a of cells.values()) {
    const x = xOf(a[0].t);
    const ri = txIndex.get(a[0].tx);
    const n = a.length;
    a.forEach((e, j) => {
      const y = yMid(ri) + (j - (n - 1) / 2) * 14;
      marker(ctx, e.ev, x, y);
      ctx.fillStyle = "#1f2937";
      ctx.fillText(labelOf(e), x + 9, y + 3);
    });
  }

  const sec2 = document.createElement("section");
  const h = document.createElement("h2");
  h.textContent = sec.name;
  const wrap = document.createElement("div");
  wrap.className = "scroll";
  wrap.appendChild(canvas);
  const pre = document.createElement("pre");
  let txt = "";
  for (const r of parsed.reclaims) {
    txt += "t=" + r.t + " RECLAIM " + r.key + " " + r.detail + "\\n";
  }
  if (parsed.summary) txt += "t=" + parsed.summary.t + " SUMMARY " + parsed.summary.detail;
  pre.textContent = txt;
  sec2.appendChild(h);
  sec2.appendChild(wrap);
  sec2.appendChild(pre);
  return sec2;
}

const app = document.getElementById("app");
for (const s of SECTIONS) app.appendChild(render(s));
</script>
</body>
</html>
"""
