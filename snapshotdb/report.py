"""把全部样例的轨迹内联成单文件 HTML 时间线页面。

页面上的位置、版本号、水位与冲突判定全部取自引擎输出的轨迹，
页面脚本只做解析与绘制，不重算任何判定。不 fetch、不引 CDN、不起服务。
"""

import json

PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>快照隔离 · 并发时间线</title>
<style>
  body { font-family: system-ui, "PingFang SC", "Microsoft YaHei", sans-serif;
         margin: 24px; color: #222; background: #f6f7f9; }
  h1 { font-size: 20px; margin: 0 0 6px; }
  .meta { color: #666; font-size: 13px; margin: 0 0 16px; }
  .legend { font-size: 12px; color: #444; background: #fff; border: 1px solid #ddd;
            border-radius: 8px; padding: 8px 12px; margin-bottom: 16px; line-height: 1.9; }
  .legend code { background: #f0f1f4; padding: 1px 5px; border-radius: 3px; }
  section { background: #fff; border: 1px solid #ddd; border-radius: 8px;
            padding: 16px; margin: 0 0 20px; overflow-x: auto; }
  h2 { font-size: 16px; margin: 0 0 8px; }
  .summary { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
             font-size: 12px; background: #eef3ff; border: 1px solid #d4e0fb;
             border-radius: 4px; padding: 6px 8px; margin-bottom: 10px; }
  .reclaims { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
              font-size: 12px; color: #555; margin-top: 10px; }
  .reclaims .head { font-weight: 600; color: #333; margin-bottom: 2px; }
  canvas { display: block; }
</style>
</head>
<body>
<h1>快照隔离 · 并发时间线</h1>
<p class="meta">由 <code>python -m snapshotdb report</code> 生成。横轴为脚本时刻 t；
每个事务一行。页面上的版本号、提交/中止判定、回收水位与计数全部取自引擎轨迹，不在页面内重算。</p>
<div class="legend">
  图例：<code>○ BEGIN s=n</code> 事务开始（快照序号）　<code>□ k@n</code> 读到版本 n（<code>k@null</code> 读到空，<code>k@-</code> 读自己的写）　
  <code>◇ k=v</code> 写入写集　<code>● COMMIT ver=n</code> 提交　<code>✕ ABORT/ROLLBACK reason</code> 中止/回滚　
  <span style="color:#c62828">红色虚线</span> 从 ABORT 指向 with= 事务的提交点，线上标触发键与 reason。
</div>
<div id="root"></div>
<script id="trace-data" type="application/json">__DATA__</script>
<script>
"use strict";
var DATA = JSON.parse(document.getElementById("trace-data").textContent);
var ROOT = document.getElementById("root");
var COLORS = { BEGIN: "#2e7d32", READ: "#1565c0", WRITE: "#e65100",
               COMMIT: "#1b5e20", ABORT: "#c62828", ROLLBACK: "#616161" };

function field(detail, name) {
  var m = new RegExp("(?:^| )" + name + "=(\\\\S+)").exec(detail);
  return m ? m[1] : "";
}

function parseTrace(lines) {
  var txns = [], byId = {}, reclaims = [], summary = null, tset = {};
  for (var i = 0; i < lines.length; i++) {
    var f = lines[i].split("|");
    var rec = { t: +f[0], op: f[1], tx: f[2], key: f[3],
                ver: f[4], val: f[5], detail: f[6] };
    if (rec.op === "SUMMARY") { summary = rec; continue; }
    tset[rec.t] = true;
    if (rec.op === "RECLAIM") { reclaims.push(rec); continue; }
    var txn = byId[rec.tx];
    if (!txn) { txn = { id: rec.tx, events: [] }; byId[rec.tx] = txn; txns.push(txn); }
    txn.events.push(rec);
  }
  var ts = Object.keys(tset).map(Number).sort(function (a, b) { return a - b; });
  return { txns: txns, byId: byId, reclaims: reclaims, summary: summary, ts: ts };
}

function labelOf(ev) {
  if (ev.op === "BEGIN") return "BEGIN s=" + field(ev.detail, "snapshot");
  if (ev.op === "READ") return ev.key + "@" + (ev.ver === "0" ? "null" : ev.ver);
  if (ev.op === "WRITE") return ev.key + "=" + ev.val;
  if (ev.op === "COMMIT") return "COMMIT ver=" + field(ev.detail, "ver");
  if (ev.op === "ABORT") return "ABORT " + field(ev.detail, "reason");
  if (ev.op === "ROLLBACK") return "ROLLBACK " + field(ev.detail, "reason");
  return ev.op;
}

function drawMarker(ctx, op, x, y) {
  ctx.strokeStyle = COLORS[op]; ctx.fillStyle = COLORS[op]; ctx.lineWidth = 1.6;
  ctx.beginPath();
  if (op === "BEGIN") { ctx.arc(x, y, 5, 0, Math.PI * 2); ctx.stroke(); }
  else if (op === "READ") { ctx.rect(x - 4.5, y - 4.5, 9, 9); ctx.stroke(); }
  else if (op === "WRITE") {
    ctx.moveTo(x, y - 6); ctx.lineTo(x + 6, y);
    ctx.lineTo(x, y + 6); ctx.lineTo(x - 6, y); ctx.closePath(); ctx.stroke();
  } else if (op === "COMMIT") { ctx.arc(x, y, 5.5, 0, Math.PI * 2); ctx.fill(); }
  else {
    ctx.moveTo(x - 5, y - 5); ctx.lineTo(x + 5, y + 5);
    ctx.moveTo(x + 5, y - 5); ctx.lineTo(x - 5, y + 5); ctx.stroke();
  }
}

function renderSection(section) {
  var parsed = parseTrace(section.trace);
  var sec = document.createElement("section");
  var h2 = document.createElement("h2");
  h2.textContent = section.name;
  sec.appendChild(h2);
  if (parsed.summary) {
    var sd = document.createElement("div");
    sd.className = "summary";
    sd.textContent = "SUMMARY(t=" + parsed.summary.t + ")  " + parsed.summary.detail;
    sec.appendChild(sd);
  }

  var W = 1160, ML = 110, MR = 40, MT = 40, RH = 78, MB = 30;
  var rows = parsed.txns.length;
  var H = MT + Math.max(rows, 1) * RH + MB;
  var canvas = document.createElement("canvas");
  var dpr = window.devicePixelRatio || 1;
  canvas.width = W * dpr; canvas.height = H * dpr;
  canvas.style.width = W + "px"; canvas.style.height = H + "px";
  var ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  ctx.font = "11px ui-monospace, Menlo, Consolas, monospace";

  var ts = parsed.ts, tmin = ts[0], tmax = ts[ts.length - 1];
  function x(t) {
    if (tmax === tmin) return ML + (W - ML - MR) / 2;
    return ML + (t - tmin) / (tmax - tmin) * (W - ML - MR);
  }
  function y(i) { return MT + i * RH + RH / 2; }

  // 横轴与刻度（刻度值取自轨迹里的 t）
  ctx.strokeStyle = "#999"; ctx.fillStyle = "#666"; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(ML, MT - 14); ctx.lineTo(W - MR, MT - 14); ctx.stroke();
  var stride = Math.max(1, Math.ceil(ts.length / 24));
  ctx.textAlign = "center";
  for (var ti = 0; ti < ts.length; ti += stride) {
    var tx0 = x(ts[ti]);
    ctx.beginPath(); ctx.moveTo(tx0, MT - 18); ctx.lineTo(tx0, MT - 10); ctx.stroke();
    ctx.fillText("t=" + ts[ti], tx0, MT - 22);
  }

  // 行与行标签
  ctx.textAlign = "right";
  for (var r = 0; r < rows; r++) {
    var ry = y(r);
    ctx.strokeStyle = "#e3e5e9";
    ctx.beginPath(); ctx.moveTo(ML, ry); ctx.lineTo(W - MR, ry); ctx.stroke();
    ctx.fillStyle = "#333";
    ctx.fillText(parsed.txns[r].id, ML - 10, ry + 4);
  }

  // 冲突连线：ABORT -> with= 事务的提交点
  function commitPointOf(txn, beforeT) {
    var best = null;
    for (var i = 0; i < txn.events.length; i++) {
      var ev = txn.events[i];
      if (ev.op === "COMMIT" && ev.t <= beforeT) best = ev;
    }
    return best;
  }
  var edges = [];
  parsed.txns.forEach(function (txn, rowIdx) {
    txn.events.forEach(function (ev) {
      if (ev.op !== "ABORT") return;
      var otherId = field(ev.detail, "with");
      var other = parsed.byId[otherId];
      if (!other) return;
      var toEv = commitPointOf(other, ev.t) || other.events[0];
      edges.push({ from: { x: x(ev.t), y: y(rowIdx) },
                   to: { x: x(toEv.t), y: y(parsed.txns.indexOf(other)) },
                   label: ev.key + " \\u00b7 " + field(ev.detail, "reason") });
    });
  });
  edges.forEach(function (e) {
    var mx = (e.from.x + e.to.x) / 2;
    ctx.strokeStyle = "#c62828"; ctx.lineWidth = 1.4; ctx.setLineDash([5, 3]);
    ctx.beginPath(); ctx.moveTo(e.from.x, e.from.y);
    ctx.bezierCurveTo(mx, e.from.y, mx, e.to.y, e.to.x, e.to.y);
    ctx.stroke(); ctx.setLineDash([]);
    var dir = e.to.x >= e.from.x ? 0 : Math.PI;
    ctx.fillStyle = "#c62828";
    ctx.beginPath();
    ctx.moveTo(e.to.x, e.to.y);
    ctx.lineTo(e.to.x - 8 * Math.cos(dir - 0.4), e.to.y - 8 * Math.sin(dir - 0.4));
    ctx.lineTo(e.to.x - 8 * Math.cos(dir + 0.4), e.to.y - 8 * Math.sin(dir + 0.4));
    ctx.closePath(); ctx.fill();
    var lx = 0.125 * e.from.x + 0.75 * mx + 0.125 * e.to.x;
    var ly = (e.from.y + e.to.y) / 2;
    ctx.font = "11px ui-monospace, Menlo, Consolas, monospace";
    var tw = ctx.measureText(e.label).width;
    ctx.fillStyle = "rgba(255,255,255,0.85)";
    ctx.fillRect(lx - tw / 2 - 3, ly - 9, tw + 6, 14);
    ctx.fillStyle = "#c62828"; ctx.textAlign = "center";
    ctx.fillText(e.label, lx, ly + 2);
  });

  // 事件标记与文字（同刻事件按车道上下错开）
  parsed.txns.forEach(function (txn, rowIdx) {
    var cy = y(rowIdx);
    txn.events.forEach(function (ev, idx) {
      var cx = x(ev.t);
      drawMarker(ctx, ev.op, cx, cy);
      var lane = idx % 6;
      var ly = lane < 3 ? cy - (16 + lane * 13) : cy + (24 + (lane - 3) * 13);
      ctx.fillStyle = COLORS[ev.op];
      ctx.textAlign = "center";
      ctx.fillText(labelOf(ev), cx, ly);
    });
  });

  sec.appendChild(canvas);

  if (parsed.reclaims.length) {
    var box = document.createElement("div");
    box.className = "reclaims";
    var head = document.createElement("div");
    head.className = "head";
    head.textContent = "RECLAIM（水位回收，取自轨迹）";
    box.appendChild(head);
    parsed.reclaims.forEach(function (rec) {
      var d = document.createElement("div");
      d.textContent = "t=" + rec.t + "  " + rec.key + "  " + rec.detail;
      box.appendChild(d);
    });
    sec.appendChild(box);
  }
  ROOT.appendChild(sec);
}

DATA.forEach(renderSection);
</script>
</body>
</html>
"""


def render_page(sections):
    """sections: [{"name": ..., "trace": [...]}]，按给定顺序内联。"""
    data = json.dumps(sections, ensure_ascii=True, separators=(",", ":"))
    return PAGE.replace("__DATA__", data, 1)
