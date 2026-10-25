"""Read-only M6 operator dashboard and audit API."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from src.common import db

app = FastAPI(title="Team 16 SDN SLA Dashboard")


def _event_dict(event) -> Dict[str, Any]:
    return json.loads(event.to_json())


def _measurement_dict(row) -> Dict[str, Any]:
    return {key: row[key] for key in ("at", "delay_ms", "jitter_ms", "loss_pct", "throughput_mbps")}


def _snapshot(conn, intent_id: str) -> Optional[Dict[str, Any]]:
    intent = db.load_intent(conn, intent_id)
    if intent is None:
        return None
    path = db.current_path(conn, intent_id)
    rows = conn.execute(
        "SELECT at, delay_ms, jitter_ms, loss_pct, throughput_mbps "
        "FROM measurements WHERE intent_id = ? ORDER BY at DESC LIMIT 60",
        (intent_id,),
    ).fetchall()
    measurements = [_measurement_dict(row) for row in reversed(rows)]
    events = [_event_dict(event) for event in db.events_for(conn, intent_id, 100)]
    return {
        "intent": {
            "id": intent.id, "tenant": intent.tenant, "priority": intent.priority,
            "state": intent.state.value, "consistency": intent.consistency.value,
            "on_violation": intent.on_violation.value,
            "guarantee": {
                "min_bandwidth_mbps": intent.guarantee.min_bandwidth_mbps,
                "max_latency_ms": intent.guarantee.max_latency_ms,
                "max_loss_pct": intent.guarantee.max_loss_pct,
            },
            "updated_at": intent.updated_at,
        },
        "path": None if path is None else {
            "switches": list(path.switches), "links": list(path.links),
            "est_latency_ms": path.est_latency_ms,
            "bottleneck_mbps": path.bottleneck_mbps,
            "version": path.version, "computed_at": path.computed_at,
        },
        "latest_measurement": measurements[-1] if measurements else None,
        "measurements": measurements, "reroutes": db.reroute_count(conn, intent_id),
        "events": events,
    }


@app.get("/api/dashboard")
def dashboard_data() -> Dict[str, Any]:
    """Return intent summaries and the most recent audit events."""
    conn = db.connect()
    try:
        db.init_db(conn)
        rows = conn.execute(
            "SELECT id FROM intents ORDER BY updated_at DESC, id LIMIT 200"
        ).fetchall()
        intents: List[Dict[str, Any]] = []
        for row in rows:
            snapshot = _snapshot(conn, row["id"])
            if snapshot is not None:
                intents.append({
                    **snapshot["intent"], "path": snapshot["path"],
                    "latest_measurement": snapshot["latest_measurement"],
                    "reroutes": snapshot["reroutes"],
                })
        events = [_event_dict(event) for event in db.events_for(conn, limit=30)]
        return {"intents": intents, "events": events}
    finally:
        conn.close()


@app.get("/api/intents/{intent_id}")
def intent_detail(intent_id: str) -> Dict[str, Any]:
    """Return one intent's path, recent measurements and audit history."""
    conn = db.connect()
    try:
        db.init_db(conn)
        result = _snapshot(conn, intent_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Unknown intent")
        return result
    finally:
        conn.close()


@app.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    """Serve a self-contained operator view with no external assets."""
    return DASHBOARD_HTML


DASHBOARD_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Team 16 SDN SLA</title><style>body{font:14px 'Segoe UI',sans-serif;background:#f2f6f8;color:#172b3a;margin:0}header{background:#102f45;color:white;padding:20px 5%;display:flex;justify-content:space-between}main{max-width:1180px;margin:24px auto;padding:0 20px}.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-bottom:16px}.card,.panel{background:white;border:1px solid #d9e3e8;border-radius:9px;padding:16px}.card small,.muted{color:#5c6d78}.card strong{display:block;font-size:26px;margin-top:6px}.layout{display:grid;grid-template-columns:1.2fr .8fr;gap:16px}h1{font-size:22px;margin:0}h2{font-size:16px;margin:0 0 12px}table{width:100%;border-collapse:collapse;font-size:13px}th,td{text-align:left;padding:9px 7px;border-bottom:1px solid #edf1f3;white-space:nowrap}.intent-btn{border:0;background:none;color:#087e8b;font-weight:bold;cursor:pointer}.badge{border-radius:20px;padding:4px 8px;background:#e8f3f3;color:#17616a;font-size:11px}.detail{margin-top:16px}.route{color:#087e8b;font-weight:650}.metrics{display:flex;gap:14px;flex-wrap:wrap;margin:10px 0;color:#5c6d78}.metrics b{color:#172b3a}.chart{width:100%;height:135px}.chart polyline{fill:none;stroke:#087e8b;stroke-width:2.5}.chart line{stroke:#e2e9ed}.events{list-style:none;margin:0;padding:0;max-height:500px;overflow:auto}.events li{padding:10px 0;border-bottom:1px solid #edf1f3}.events time{display:block;color:#5c6d78;font-size:11px;margin-bottom:4px}footer{text-align:right;color:#5c6d78;font-size:11px;margin:14px}@media(max-width:780px){.layout{grid-template-columns:1fr}}@media(max-width:500px){.cards{grid-template-columns:1fr}}</style></head>
<body><header><h1>Intent SDN SLA</h1><span>Team 16 · operator dashboard</span></header><main>
<section class="cards"><article class="card"><small>Tracked intents</small><strong id="count">—</strong></article><article class="card"><small>Active or admitted</small><strong id="active">—</strong></article><article class="card"><small>Recent audit events</small><strong id="event-count">—</strong></article></section>
<section class="layout"><article class="panel"><h2>Intents</h2><div style="overflow:auto"><table><thead><tr><th>Intent</th><th>Tenant</th><th>Priority</th><th>State</th><th>Current path</th></tr></thead><tbody id="intents"></tbody></table></div>
<div class="detail"><h2 id="detail-title">Select an intent</h2><div id="detail-content" class="muted">Choose an intent to inspect its SLA, measurements and route.</div>
<svg class="chart" viewBox="0 0 600 140" role="img" aria-label="Recent measurements"><line x1="0" y1="125" x2="600" y2="125"/><line x1="0" y1="70" x2="600" y2="70"/><line x1="0" y1="15" x2="600" y2="15"/><polyline id="plot" points=""/></svg>
<label for="metric">Chart metric </label><select id="metric"><option value="delay_ms">Delay (ms)</option><option value="jitter_ms">Jitter (ms)</option><option value="loss_pct">Loss (%)</option><option value="throughput_mbps">Throughput (Mbps)</option></select></div></article>
<aside class="panel"><h2>Audit trail</h2><ul class="events" id="events"></ul></aside></section><footer id="updated">Waiting for database…</footer></main>
<script>
let selected=null,detail=null;const text=(id,v)=>document.getElementById(id).textContent=v;function when(v){return v?new Date(v*1000).toLocaleString():"—"}
function draw(){if(!detail)return;const key=document.getElementById("metric").value,values=detail.measurements.map(x=>x[key]).filter(x=>x!=null);if(!values.length){document.getElementById("plot").setAttribute("points","");return}const lo=Math.min(...values),span=Math.max(...values)-lo||1;document.getElementById("plot").setAttribute("points",values.map((v,i)=>`${values.length<2?300:i*600/(values.length-1)},${125-(v-lo)/span*110}`).join(" "))}
function showEvents(items){const list=document.getElementById("events");list.replaceChildren();for(const e of items){const li=document.createElement("li"),time=document.createElement("time"),msg=document.createElement("div");time.textContent=`${when(e.at)} · ${e.severity} · ${e.type}`;msg.textContent=e.message;li.append(time,msg);list.append(li)}if(!items.length){const li=document.createElement("li");li.textContent="No audit events recorded yet.";list.append(li)}}
async function selectIntent(id){selected=id;const r=await fetch(`/api/intents/${encodeURIComponent(id)}`);if(!r.ok)return;detail=await r.json();const i=detail.intent,p=detail.path,m=detail.latest_measurement,g=i.guarantee;text("detail-title",`${i.id} · ${i.tenant}`);const box=document.getElementById("detail-content");box.replaceChildren();const route=document.createElement("p");route.className="route";route.textContent=p?`${p.switches.join(" → ")} · version ${p.version??"direct"}`:"No path installed";const metrics=document.createElement("div");metrics.className="metrics";for(const [k,v] of [["State",i.state],["Delay",m?.delay_ms==null?"—":m.delay_ms.toFixed(2)+" ms"],["Jitter",m?.jitter_ms==null?"—":m.jitter_ms.toFixed(2)+" ms"],["Loss",m?.loss_pct==null?"—":m.loss_pct.toFixed(2)+"%"],["Throughput",m?.throughput_mbps==null?"—":m.throughput_mbps.toFixed(3)+" Mbps"],["Reroutes",detail.reroutes]]){const el=document.createElement("span"),b=document.createElement("b");el.append(`${k}: `);b.textContent=v;el.append(b);metrics.append(el)}const promise=document.createElement("p");promise.className="muted";promise.textContent=`SLA: min bandwidth ${g.min_bandwidth_mbps??"—"} Mbps · max delay ${g.max_latency_ms??"—"} ms · max loss ${g.max_loss_pct??"—"}%`;box.append(route,metrics,promise);showEvents(detail.events);draw()}
async function refresh(){try{const data=await(await fetch("/api/dashboard",{cache:"no-store"})).json();text("count",data.intents.length);text("active",data.intents.filter(i=>["active","admitted","degraded"].includes(i.state)).length);text("event-count",data.events.length);const body=document.getElementById("intents");body.replaceChildren();for(const i of data.intents){const row=document.createElement("tr");for(const value of [i.id,i.tenant,i.priority]){const cell=document.createElement("td");if(value===i.id){const b=document.createElement("button");b.className="intent-btn";b.textContent=i.id;b.onclick=()=>selectIntent(i.id);cell.append(b)}else cell.textContent=value;row.append(cell)}const state=document.createElement("td"),badge=document.createElement("span");badge.className=`badge ${i.state}`;badge.textContent=i.state;state.append(badge);row.append(state);const path=document.createElement("td");path.textContent=i.path?i.path.switches.join(" → "):"—";row.append(path);body.append(row)}if(selected)await selectIntent(selected);else showEvents(data.events);text("updated",`Updated ${new Date().toLocaleTimeString()} · read-only`)}catch(_){text("updated","Dashboard could not read the local state database.")}}
document.getElementById("metric").addEventListener("change",draw);refresh();setInterval(refresh,3000);
</script></body></html>"""
