import os
import time
from datetime import datetime, timezone
from threading import Lock, Thread

from flask import Flask, jsonify, render_template_string

app = Flask(__name__)
_state_lock = Lock()
_started_at = time.time()
_bot_state = {
    "connected": False,
    "user": None,
    "user_id": None,
    "guild_count": 0,
    "latency_ms": None,
    "last_ready_at": None,
    "last_error": None,
    "status": "starting",
}

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Agni • Discord Bot</title>
<style>
:root{color-scheme:dark;--bg:#090b12;--panel:#121725;--muted:#9ba7bd;--line:#273148;--accent:#8b5cf6;--green:#35d49a}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(ellipse at 50% -20%,#30205b 0,transparent 55%),var(--bg);color:#f4f6ff;font:16px Inter,system-ui,sans-serif;min-height:100vh}
main{max-width:980px;margin:auto;padding:72px 22px}.top{display:flex;align-items:center;gap:12px;color:#c4b5fd;font-weight:700;letter-spacing:.14em;text-transform:uppercase;font-size:12px}
.logo{width:38px;height:38px;border-radius:12px;background:linear-gradient(135deg,#a78bfa,#6d28d9);display:grid;place-items:center;color:white;font-size:19px}
h1{font-size:clamp(42px,8vw,72px);letter-spacing:-.06em;line-height:1;margin:38px 0 14px}.sub{color:var(--muted);max-width:620px;line-height:1.7}
.panel{margin-top:38px;background:linear-gradient(145deg,#151c2d,#101522);border:1px solid var(--line);border-radius:24px;padding:26px;box-shadow:0 24px 80px #0006}
.status{display:flex;align-items:center;gap:12px;font-size:22px;font-weight:750}.dot{width:12px;height:12px;border-radius:50%;background:#e7a34b;box-shadow:0 0 18px currentColor}.dot.online{background:var(--green);color:var(--green)}.dot.offline{background:#f87171;color:#f87171}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-top:24px}.metric{background:#0b101b;border:1px solid var(--line);border-radius:16px;padding:18px}.metric small{color:var(--muted);display:block;margin-bottom:8px}.metric strong{font-size:24px}
.foot{color:#78849b;font-size:12px;margin-top:24px}.pill{display:inline-block;border:1px solid var(--line);border-radius:99px;padding:6px 10px;color:var(--muted);font-size:12px;margin-top:18px}
</style></head><body><main><div class="top"><div class="logo">A</div> AGNI SYSTEMS</div>
<h1>Built to<br><span style="color:#a78bfa">moderate.</span></h1><p class="sub">A multi-purpose Discord community bot for moderation, tickets, giveaways, welcome tools and server protection.</p>
<section class="panel"><div class="status"><span id="dot" class="dot"></span><span id="status">Checking Discord connection…</span></div>
<div class="grid"><div class="metric"><small>Servers</small><strong id="guilds">—</strong></div><div class="metric"><small>Gateway latency</small><strong id="latency">—</strong></div><div class="metric"><small>Process uptime</small><strong id="uptime">—</strong></div><div class="metric"><small>Last connected</small><strong id="lastready">—</strong></div></div>
<div class="pill">Auto-refreshes every 15 seconds</div></section><div class="foot">Agni Discord Bot · <a style="color:#a78bfa" href="/api/status">Live status API</a> · <a style="color:#a78bfa;font-weight:700" href="/dashboard">Open Control Panel →</a></div></main>
<script>
const $=id=>document.getElementById(id);
function duration(s){s=Math.max(0,Math.floor(s));let d=Math.floor(s/86400);s%=86400;let h=Math.floor(s/3600);s%=3600;let m=Math.floor(s/60);return d?d+"d "+h+"h":h?h+"h "+m+"m":m+"m "+s+"s"}
async function refresh(){try{const r=await fetch('/api/status',{cache:'no-store'});const d=await r.json();$('dot').className='dot '+(d.discord_connected?'online':'offline');$('status').textContent=d.discord_connected?'Discord bot online':'Discord bot offline';$('guilds').textContent=d.guild_count??'—';$('latency').textContent=d.latency_ms==null?'—':d.latency_ms+' ms';$('uptime').textContent=duration(d.process_uptime_seconds||0);$('lastready').textContent=d.last_ready_at?new Date(d.last_ready_at).toLocaleString():'Not connected yet'}catch(e){$('status').textContent='Status unavailable';$('dot').className='dot offline'}}
refresh();setInterval(refresh,15000);
</script></body></html>"""


def set_bot_status(*, connected=False, user=None, guild_count=0, latency_ms=None, error=None):
    with _state_lock:
        _bot_state.update({
            "connected": bool(connected),
            "user": str(user) if user else None,
            "user_id": getattr(user, "id", None),
            "guild_count": int(guild_count or 0),
            "latency_ms": round(float(latency_ms), 1) if latency_ms is not None else None,
            "last_error": str(error)[:500] if error else None,
            "status": "online" if connected else "offline",
        })
        if connected:
            _bot_state["last_ready_at"] = datetime.now(timezone.utc).isoformat()


@app.get("/")
def home():
    return render_template_string(PAGE)


@app.get("/api/status")
def api_status():
    with _state_lock:
        state = dict(_bot_state)
    state.update({
        "service": "Agni Discord Bot",
        "discord_connected": state["connected"],
        "process_uptime_seconds": int(time.time() - _started_at),
        "checked_at": datetime.now(timezone.utc).isoformat(),
    })
    return jsonify(state)


@app.get("/health")
def health():
    # Render process health only; Discord gateway state is reported separately by /api/status.
    return jsonify(status="ok", service="web"), 200


def run():
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False, threaded=True)


def keep_alive():
    thread = Thread(target=run, name="agni-health-server", daemon=True)
    thread.start()
