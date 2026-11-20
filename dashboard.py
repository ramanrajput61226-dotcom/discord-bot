"""Secure Discord OAuth control panel for Agni.

Required environment variables:
DISCORD_CLIENT_ID, DISCORD_CLIENT_SECRET, DASHBOARD_SECRET_KEY.
The bot token is never sent to the browser.
"""
import json
import os
import secrets
import time
import urllib.parse
import urllib.request
from flask import Blueprint, jsonify, redirect, render_template_string, request, session, url_for

bp = Blueprint("agni_dashboard", __name__)
_bot = None
_db = None
_ticket_configs = None
_save_settings = None
_ensure_ticket_structure = None
_started = time.time()

FIELDS = {
    "welcome": {
        "title": "Welcome & onboarding",
        "keys": ["welcome_enabled", "welcome_channel_id", "welcome_message", "welcome_image", "goodbye_channel_id", "goodbye_message", "autorole_id", "verification_channel_id", "verification_role_id"],
    },
    "tickets": {
        "title": "Ticket system",
        "keys": ["ticket_log_channel_id"],
    },
    "moderation": {
        "title": "Moderation & AutoMod",
        "keys": ["automod_enabled", "bad_words", "link_filter", "caps_filter", "mention_limit", "automod_action", "automod_spam", "automod_duplicate", "automod_invites", "automod_max_message", "automod_log_channel_id"],
    },
    "protection": {
        "title": "Raid & anti-nuke protection",
        "keys": ["raid_enabled", "raid_threshold", "raid_window", "raid_action", "nuke_ban_limit", "nuke_channel_limit", "nuke_role_limit"],
    },
    "logging": {
        "title": "Server logs",
        "keys": ["log_channel_id", "member_log_channel_id", "message_log_channel_id", "voice_log_channel_id", "channel_log_channel_id", "role_log_channel_id", "invite_log_channel_id"],
    },
    "community": {
        "title": "Community tools",
        "keys": ["suggestion_channel_id", "poll_channel_id", "reaction_roles"],
    },
}
ALLOWED = {key for group in FIELDS.values() for key in group["keys"]}


def _discord_api(path, token=None, method="GET", data=None, bot_auth=False):
    headers = {"User-Agent": "AgniDashboard/1.0"}
    if bot_auth:
        headers["Authorization"] = "Bot " + os.getenv("DISCORD_TOKEN", "")
    elif token:
        headers["Authorization"] = "Bearer " + token
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request("https://discord.com/api/v10" + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=12) as response:
        return json.loads(response.read().decode("utf-8"))


def _json_error(message, status=400):
    return jsonify({"error": message}), status


def _guild_access(guild_id):
    if not session.get("discord_token") or not session.get("user"):
        return None, _json_error("Please sign in with Discord first.", 401)
    try:
        guilds = session.get("guilds") or _discord_api("/users/@me/guilds", session["discord_token"])
        session["guilds"] = guilds
        target = next((g for g in guilds if str(g.get("id")) == str(guild_id)), None)
        if not target:
            return None, _json_error("You do not have access to this server.", 403)
        permissions = int(target.get("permissions", "0"))
        if not (permissions & 0x20 or permissions & 0x8):
            return None, _json_error("Manage Server or Administrator permission is required.", 403)
        guild = _bot.get_guild(int(guild_id)) if _bot else None
        if guild is None:
            return None, _json_error("Agni is not currently in this server.", 404)
        return guild, None
    except Exception:
        return None, _json_error("Could not verify Discord permissions. Please sign in again.", 401)


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Agni Control Panel</title>
<style>
:root{color-scheme:dark;--bg:#080b13;--panel:#111827;--panel2:#151e30;--line:#26334a;--muted:#98a7c0;--text:#f4f6ff;--accent:#9b75ff;--green:#38d6a0}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(ellipse at 80% -20%,#38236b 0,transparent 40%),var(--bg);color:var(--text);font:15px Inter,system-ui,sans-serif}
a{color:inherit;text-decoration:none}button,select,input,textarea{font:inherit}button{cursor:pointer}.shell{max-width:1320px;margin:auto;padding:24px}.bar{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:10px 0 26px;border-bottom:1px solid var(--line)}.brand{font-weight:900;letter-spacing:.18em;color:#c8b5ff}.brand span{display:inline-grid;place-items:center;background:linear-gradient(135deg,#a78bfa,#6d28d9);width:38px;height:38px;border-radius:12px;margin-right:10px;color:white}.muted{color:var(--muted)}.btn{border:1px solid var(--line);background:#1b2740;color:white;border-radius:11px;padding:11px 16px}.primary{background:linear-gradient(135deg,#8b5cf6,#6d43db);border:0}.layout{display:grid;grid-template-columns:250px 1fr;gap:22px;margin-top:24px}.side,.card{background:#101725;border:1px solid var(--line);border-radius:18px}.side{padding:16px;height:max-content;position:sticky;top:16px}.nav{display:block;width:100%;text-align:left;background:transparent;border:0;color:#b7c2d6;padding:12px;border-radius:10px;margin:3px 0}.nav.active,.nav:hover{background:#282047;color:white}.content h1{font-size:30px;margin:4px 0}.hero{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:20px}.card{padding:20px;margin:14px 0}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.field label{display:block;color:#bac6d9;font-size:13px;margin-bottom:7px}.field input,.field select,.field textarea{width:100%;background:#090f1b;border:1px solid #2a3851;border-radius:10px;color:white;padding:11px}.field textarea{min-height:85px;resize:vertical}.field small{display:block;color:var(--muted);margin-top:5px}.actions{display:flex;justify-content:flex-end;gap:10px;margin-top:18px}.metric{padding:15px;background:#0b1220;border:1px solid var(--line);border-radius:13px}.metric b{display:block;font-size:22px;margin-top:6px}.pill{font-size:12px;color:#c7b5ff;border:1px solid #4d3c79;padding:6px 10px;border-radius:99px}.notice{padding:14px;border-radius:12px;background:#211c37;color:#d7cbff;margin:14px 0}.hidden{display:none}.empty{padding:55px 20px;text-align:center}.footer{padding:22px 0;color:#74829b;font-size:12px}@media(max-width:760px){.shell{padding:14px}.layout{grid-template-columns:1fr}.side{position:static;display:flex;overflow:auto;gap:4px}.nav{white-space:nowrap;width:auto}.grid{grid-template-columns:1fr}.hero{align-items:flex-start;flex-direction:column}}
</style></head><body><div class="shell"><header class="bar"><div class="brand"><span>A</span>AGNI / CONTROL</div><div id="userbox" class="muted">Secure dashboard</div></header>
<div id="login" class="empty card"><div class="pill">AGNI CONTROL CENTER</div><h1>One dashboard. Full control.</h1><p class="muted">Manage tickets, welcome, moderation, protection and community settings for your Discord server.</p><div id="loginmsg" class="notice">Sign in with Discord to continue. Server access is permission-checked.</div><a class="btn primary" href="/auth/login">Continue with Discord</a></div>
<div id="app" class="layout hidden"><aside class="side"><div class="muted" style="padding:8px">YOUR SERVER</div><select id="guild" class="btn" style="width:100%;margin-bottom:16px" onchange="loadConfig()"></select><div id="nav"></div><button class="nav" onclick="location.href='/auth/logout'">↪ Sign out</button></aside><main class="content"><div class="hero"><div><div class="pill">LIVE CONTROL PANEL</div><h1 id="heading">Dashboard</h1><div class="muted" id="subheading">Choose a module to configure your server.</div></div><div id="botstatus" class="pill">Checking bot…</div></div><div id="notice"></div><div id="forms"></div><div class="footer">Agni Control Panel · Changes are saved to the bot's persistent configuration.</div></main></div></div>
<script>
const modules={welcome:'Welcome & onboarding',tickets:'Ticket system',moderation:'Moderation & AutoMod',protection:'Raid & anti-nuke protection',logging:'Server logs',community:'Community tools'};
let me=null,guilds=[],config={},channels=[],roles=[],active='welcome';
const $=id=>document.getElementById(id);
async function api(url,opts={}){const r=await fetch(url,{headers:{'Content-Type':'application/json'},...opts});const d=await r.json();if(!r.ok)throw Error(d.error||'Request failed');return d}
function field(key,value){const label=key.replaceAll('_',' ').replace(/\b\w/g,c=>c.toUpperCase());let input='';if(key.endsWith('_channel_id')||key.endsWith('_role_id')){const list=key.endsWith('_role_id')?roles:channels;input='<select id="f_'+key+'"><option value="">Not set</option>'+list.map(x=>'<option value="'+x.id+'" '+(String(value??'')===String(x.id)?'selected':'')+'>'+esc(x.name)+'</option>').join('')+'</select>'}else if(['welcome_enabled','automod_enabled','link_filter','caps_filter','automod_spam','automod_duplicate','automod_invites','raid_enabled'].includes(key)){input='<select id="f_'+key+'"><option value="0" '+(!value?'selected':'')+'>Disabled</option><option value="1" '+(value?'selected':'')+'>Enabled</option></select>'}else if(key==='automod_action'||key==='raid_action'){const vals=key==='automod_action'?['delete','warn','timeout']:['lockdown','log'];input='<select id="f_'+key+'">'+vals.map(v=>'<option '+(value===v?'selected':'')+'>'+v+'</option>').join('')+'</select>'}else if(key==='welcome_message'||key==='goodbye_message'||key==='bad_words'||key==='reaction_roles'){input='<textarea id="f_'+key+'">'+esc(value??'')+'</textarea>'}else{input='<input id="f_'+key+'" value="'+esc(value??'')+'" '+(['mention_limit','automod_max_message','raid_threshold','raid_window','nuke_ban_limit','nuke_channel_limit','nuke_role_limit'].includes(key)?'type="number" min="0"':'')+'>'}return '<div class="field"><label>'+label+'</label>'+input+'</div>'}
function esc(v){return String(v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function render(){const m=config.modules[active];$('heading').textContent=modules[active];$('subheading').textContent='Customize settings for '+config.guild.name+'.';if(active==='tickets'){loadTickets();return}$('forms').innerHTML='<section class="card"><div class="grid">'+m.keys.map(k=>field(k,config.settings[k])).join('')+'</div><div class="actions"><button class="btn primary" onclick="save()">Save changes</button></div></section>'}
function nav(){ $('nav').innerHTML=Object.entries(modules).map(([k,v])=>'<button class="nav '+(k===active?'active':'')+'" onclick="active=\''+k+'\';nav();render()">'+v+'</button>').join('')}
async function loadConfig(){try{config=await api('/api/guild/'+$('guild').value+'/config');channels=config.channels;roles=config.roles;nav();render()}catch(e){$('notice').innerHTML='<div class="notice">'+esc(e.message)+'</div>'}}
async function loadTickets(){try{const d=await api('/api/guild/'+$('guild').value+'/tickets');config.categories=d.categories||[];const panels=d.panels||{};const ids=Object.keys(panels);const id=d.active_panel||ids[0]||'';const p=panels[id]||{};$('forms').innerHTML='<section class="card"><div class="hero"><div><h2>Ticket panel builder</h2><p class="muted">Edit panel copy, category, support role and button label.</p></div><button class="btn" onclick="createTicketPanel()">＋ New panel</button></div><div class="field"><label>Panel</label><select id="ticket_panel" onchange="showTicketPanel()">'+ids.map(x=>'<option value="'+esc(x)+'" '+(x===id?'selected':'')+'>'+esc(panels[x].title||x)+'</option>').join('')+'</select></div><div class="grid" style="margin-top:16px">'+ticketFields(p)+'</div><div class="actions"><button class="btn primary" onclick="saveTicketPanel()">Save ticket panel</button></div></section>';}catch(e){$('forms').innerHTML='<div class="notice">'+esc(e.message)+'</div>'}}\nfunction ticketFields(p){const opts=(id,items)=>'<select id="'+id+'"><option value="">Not set</option>'+items.map(x=>'<option value="'+x.id+'" '+(String(p[id==='ticket_category'?'category_id':'role_id']??'')===String(x.id)?'selected':'')+'>'+esc(x.name)+'</option>').join('')+'</select>';return '<div class="field"><label>Panel title</label><input id="ticket_title" value="'+esc(p.title||'Support Hub')+'"></div><div class="field"><label>Panel description</label><textarea id="ticket_desc">'+esc(p.desc||'')+'</textarea></div><div class="field"><label>Ticket title template</label><input id="ticket_title_template" value="'+esc(p.ticket_title||'🎫 {type} Ticket')+'"></div><div class="field"><label>Ticket description</label><textarea id="ticket_description">'+esc(p.ticket_desc||'')+'</textarea></div><div class="field"><label>Open message</label><input id="ticket_open_message" value="'+esc(p.ticket_open_message||'')+'"></div><div class="field"><label>Footer</label><input id="ticket_footer" value="'+esc(p.ticket_footer||'')+'"></div><div class="field"><label>Category</label>'+opts('ticket_category',config.categories||[])+'</div><div class="field"><label>Support role</label>'+opts('ticket_role',roles)+'</div><div class="field"><label>Button label</label><input id="ticket_button" value="'+esc(Object.values(p.buttons||{})[0]?.label||'Support')+'"></div>'}\nasync function showTicketPanel(){await loadTickets()}\nasync function createTicketPanel(){try{await api('/api/guild/'+$('guild').value+'/tickets',{method:'POST',body:'{}'});await loadTickets()}catch(e){$('notice').innerHTML='<div class="notice">'+esc(e.message)+'</div>'}}\nasync function saveTicketPanel(){try{const payload={panel_id:$('ticket_panel').value,title:$('ticket_title').value,desc:$('ticket_desc').value,ticket_title:$('ticket_title_template').value,ticket_desc:$('ticket_description').value,ticket_open_message:$('ticket_open_message').value,ticket_footer:$('ticket_footer').value,category_id:$('ticket_category').value,role_id:$('ticket_role').value,button_label:$('ticket_button').value};await api('/api/guild/'+$('guild').value+'/tickets',{method:'PATCH',body:JSON.stringify(payload)});$('notice').innerHTML='<div class="notice">✓ Ticket panel saved.</div>';await loadTickets()}catch(e){$('notice').innerHTML='<div class="notice">'+esc(e.message)+'</div>'}}\nasync function save(){try{const m=config.modules[active],values={};for(const k of m.keys){const el=$('f_'+k);if(!el)continue;values[k]=el.value}await api('/api/guild/'+$('guild').value+'/config',{method:'PATCH',body:JSON.stringify(values)});$('notice').innerHTML='<div class="notice">✓ Saved. Agni will use the updated settings.</div>';await loadConfig()}catch(e){$('notice').innerHTML='<div class="notice">'+esc(e.message)+'</div>'}}
async function start(){try{const d=await api('/api/me');me=d.user;guilds=d.guilds;$('login').classList.add('hidden');$('app').classList.remove('hidden');$('userbox').textContent=me.username+' · signed in';$('guild').innerHTML=guilds.map(g=>'<option value="'+g.id+'">'+esc(g.name)+'</option>').join('');$('botstatus').textContent=d.bot.connected?'● Bot online':'● Bot offline';if(guilds.length)await loadConfig();else $('forms').innerHTML='<div class="card">No servers found where you have Manage Server permission and Agni is installed.</div>'}catch(e){$('loginmsg').textContent=e.message}}
start();
</script></body></html>"""


def register_dashboard(app, bot, db, ticket_configs=None, save_settings=None, ensure_ticket_structure=None):
    global _bot, _db, _ticket_configs, _save_settings, _ensure_ticket_structure
    _bot, _db = bot, db
    _ticket_configs, _save_settings, _ensure_ticket_structure = ticket_configs, save_settings, ensure_ticket_structure
    app.secret_key = os.getenv("DASHBOARD_SECRET_KEY") or secrets.token_urlsafe(48)
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_SECURE=True)
    app.register_blueprint(bp)


@bp.get("/dashboard")
def dashboard_home():
    return render_template_string(PAGE)


@bp.get("/auth/login")
def oauth_login():
    if not os.getenv("DISCORD_CLIENT_ID") or not os.getenv("DISCORD_CLIENT_SECRET"):
        return "Dashboard OAuth is not configured. Add DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET in Render environment settings.", 503
    state = secrets.token_urlsafe(32)
    session["oauth_state"] = state
    params = urllib.parse.urlencode({"client_id": os.environ["DISCORD_CLIENT_ID"], "redirect_uri": request.url_root.rstrip("/") + "/auth/callback", "response_type": "code", "scope": "identify guilds", "state": state, "prompt": "consent"})
    return redirect("https://discord.com/oauth2/authorize?" + params)


@bp.get("/auth/callback")
def oauth_callback():
    if request.args.get("state") != session.pop("oauth_state", None):
        return "Invalid OAuth state. Please try again.", 400
    code = request.args.get("code")
    if not code:
        return "Discord authorization was cancelled.", 400
    try:
        form = urllib.parse.urlencode({"client_id": os.environ["DISCORD_CLIENT_ID"], "client_secret": os.environ["DISCORD_CLIENT_SECRET"], "grant_type": "authorization_code", "code": code, "redirect_uri": request.url_root.rstrip("/") + "/auth/callback"}).encode()
        token = _discord_api("/oauth2/token", method="POST", data=form)
        session["discord_token"] = token["access_token"]
        session["user"] = _discord_api("/users/@me", session["discord_token"])
        session["guilds"] = _discord_api("/users/@me/guilds", session["discord_token"])
        return redirect("/dashboard")
    except Exception:
        session.clear()
        return "Discord sign-in failed. Check OAuth redirect URL and credentials in Render.", 502


@bp.get("/auth/logout")
def oauth_logout():
    session.clear()
    return redirect("/dashboard")


@bp.get("/api/me")
def api_me():
    if not session.get("discord_token"):
        return _json_error("Not signed in.", 401)
    try:
        guilds = session.get("guilds") or _discord_api("/users/@me/guilds", session["discord_token"])
        allowed = [g for g in guilds if int(g.get("permissions", "0")) & (0x20 | 0x8) and _bot.get_guild(int(g["id"]))]
        return jsonify(user=session.get("user"), guilds=[{"id":g["id"],"name":g["name"],"icon":g.get("icon")} for g in allowed], bot={"connected": bool(_bot.is_ready()),"guild_count":len(_bot.guilds)})
    except Exception:
        return _json_error("Discord session expired. Sign in again.", 401)


@bp.get("/api/guild/<int:guild_id>/config")
def api_guild_config(guild_id):
    guild, error = _guild_access(guild_id)
    if error:
        return error
    try:
        columns = {row[1] for row in _db.execute("PRAGMA table_info(guild_settings)").fetchall()}
        keys = ALLOWED & columns
        row = _db.execute("SELECT * FROM guild_settings WHERE guild_id=?", (guild_id,)).fetchone()
        settings = dict(row) if row else {}
        channels = [{"id":str(c.id),"name":"#"+c.name} for c in guild.text_channels]
        roles = [{"id":str(r.id),"name":r.name} for r in guild.roles if not r.is_default() and not r.managed]
        return jsonify(guild={"id":str(guild.id),"name":guild.name},settings={k:settings.get(k) for k in keys},channels=channels,roles=roles,modules=FIELDS)
    except Exception:
        return _json_error("Could not load server settings.", 500)


@bp.patch("/api/guild/<int:guild_id>/config")
def api_update_config(guild_id):
    guild, error = _guild_access(guild_id)
    if error:
        return error
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or not payload or len(payload) > 40:
        return _json_error("Invalid settings payload.")
    columns = {row[1] for row in _db.execute("PRAGMA table_info(guild_settings)").fetchall()}
    updates = {}
    for key, value in payload.items():
        if key not in ALLOWED or key not in columns:
            return _json_error("Unsupported setting: " + str(key))
        if key.endswith("_channel_id") or key.endswith("_role_id"):
            if value in ("", None):
                updates[key] = None
            else:
                try:
                    ident = int(value)
                except (TypeError, ValueError):
                    return _json_error("Invalid channel or role selection.")
                target = guild.get_channel(ident) if key.endswith("_channel_id") else guild.get_role(ident)
                if target is None:
                    return _json_error("Selected channel or role does not belong to this server.")
                updates[key] = ident
        elif key in {"welcome_enabled","automod_enabled","link_filter","caps_filter","automod_spam","automod_duplicate","automod_invites","raid_enabled"}:
            if str(value) not in {"0","1","True","False","true","false"}:
                return _json_error("Toggle values must be enabled or disabled.")
            updates[key] = int(str(value).lower() in {"1","true"})
        elif key in {"mention_limit","automod_max_message","raid_threshold","raid_window","nuke_ban_limit","nuke_channel_limit","nuke_role_limit"}:
            try:
                number = int(value)
                if number < 0 or number > 100000:
                    raise ValueError()
                updates[key] = number
            except (TypeError, ValueError):
                return _json_error("Numeric settings must be between 0 and 100000.")
        elif key in {"automod_action","raid_action"}:
            allowed = {"delete","warn","timeout"} if key == "automod_action" else {"lockdown","log"}
            if value not in allowed:
                return _json_error("Invalid action.")
            updates[key] = value
        elif key in {"welcome_message","goodbye_message","bad_words","reaction_roles","welcome_image"}:
            if not isinstance(value, str) or len(value) > 4000:
                return _json_error("Text settings must be under 4000 characters.")
            updates[key] = value
    if not updates:
        return _json_error("No valid settings supplied.")
    try:
        _db.execute("INSERT OR IGNORE INTO guild_settings(guild_id) VALUES (?)", (guild_id,))
        assignments = ", ".join(f'"{k}"=?' for k in updates)
        _db.execute(f"UPDATE guild_settings SET {assignments} WHERE guild_id=?", (*updates.values(), guild_id))
        _db.commit()
        return jsonify(saved=list(updates.keys()))
    except Exception:
        _db.rollback()
        return _json_error("Could not save settings.", 500)


@bp.get("/api/guild/<int:guild_id>/tickets")
def api_get_tickets(guild_id):
    guild, error = _guild_access(guild_id)
    if error:
        return error
    if _ticket_configs is None:
        return _json_error("Ticket panel editor is unavailable.", 503)
    data = _ensure_ticket_structure(guild_id) if _ensure_ticket_structure else _ticket_configs.get(guild_id)
    return jsonify(active_panel=(data or {}).get("active_panel"), panels=(data or {}).get("panels", {}), categories=[{"id":str(c.id),"name":c.name} for c in guild.categories])


@bp.post("/api/guild/<int:guild_id>/tickets")
def api_create_ticket_panel(guild_id):
    guild, error = _guild_access(guild_id)
    if error:
        return error
    if _ticket_configs is None:
        return _json_error("Ticket panel editor is unavailable.", 503)
    data = _ensure_ticket_structure(guild_id) if _ensure_ticket_structure else _ticket_configs.setdefault(guild_id, {})
    if not data:
        data = {"active_panel": None, "panels": {}}
        _ticket_configs[guild_id] = data
    import secrets as _secrets
    panel_id = "panel_" + _secrets.token_hex(4)
    data.setdefault("panels", {})[panel_id] = {"title":"Support Hub","desc":"Choose a ticket type below.","ticket_title":"🎫 {type} Ticket","ticket_desc":"Ticket opened by {user}","ticket_open_message":"{role} New ticket opened by {user}","ticket_footer":"Opened by {user}","ticket_color":3447003,"category_id":None,"role_id":None,"buttons":{"button_"+_secrets.token_hex(3):{"label":"Support","questions":[]}}}
    data["active_panel"] = panel_id
    if _save_settings:
        _save_settings()
    return jsonify(panel_id=panel_id), 201


@bp.patch("/api/guild/<int:guild_id>/tickets")
def api_update_ticket_panel(guild_id):
    guild, error = _guild_access(guild_id)
    if error:
        return error
    if _ticket_configs is None:
        return _json_error("Ticket panel editor is unavailable.", 503)
    payload = request.get_json(silent=True) or {}
    data = _ensure_ticket_structure(guild_id) if _ensure_ticket_structure else _ticket_configs.get(guild_id)
    panel_id = str(payload.get("panel_id", ""))
    panel = (data or {}).get("panels", {}).get(panel_id)
    if not panel:
        return _json_error("Ticket panel not found.", 404)
    limits = {"title":100,"desc":1000,"ticket_title":200,"ticket_desc":1500,"ticket_open_message":500,"ticket_footer":200,"button_label":80}
    for key, limit in limits.items():
        if key in payload:
            value = payload[key]
            if not isinstance(value, str) or len(value) > limit:
                return _json_error(key + " is too long or invalid.")
    category_id = payload.get("category_id") or None
    role_id = payload.get("role_id") or None
    if category_id:
        try:
            category = guild.get_channel(int(category_id))
            if category is None or not getattr(category, "type", None) or category.type.name != "category":
                return _json_error("Select a category from this server.")
        except (TypeError, ValueError):
            return _json_error("Invalid category.")
    if role_id:
        try:
            if guild.get_role(int(role_id)) is None:
                return _json_error("Select a support role from this server.")
        except (TypeError, ValueError):
            return _json_error("Invalid support role.")
    for key in ("title","desc","ticket_title","ticket_desc","ticket_open_message","ticket_footer"):
        if key in payload:
            panel[key] = payload[key]
    panel["category_id"] = int(category_id) if category_id else None
    panel["role_id"] = int(role_id) if role_id else None
    buttons = panel.setdefault("buttons", {})
    if buttons and payload.get("button_label") is not None:
        first = next(iter(buttons))
        buttons[first]["label"] = payload["button_label"]
    if _save_settings:
        _save_settings()
    return jsonify(saved=True, panel_id=panel_id)
