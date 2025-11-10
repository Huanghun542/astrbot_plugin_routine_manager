# webui.py  —— 建议整文件替换
import os, asyncio, json
from quart import Quart, request, redirect, url_for, session, send_from_directory, Response, jsonify
import hypercorn.asyncio
from hypercorn.config import Config
import time

app = Quart(__name__)

SERVER_LOGIN_KEY = ""                              # 由 main 传入
ASSETS_DIR = os.path.join(os.path.dirname(__file__), "assets")
STORAGE_PATH = None                                # 由 main 传入
INITIAL_CONFIG = {}                                # 由 main 传入
WEEK_KEYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
ONE_TIME_KEY = True
KEY_EXPIRES_AT = 0.0

# ---------- 内置登录页（assets/login.html 不存在时使用） ----------
def _inline_login_html(error: str = "") -> str:
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Routine Manager 登录</title>
<style>
body{{font-family:system-ui,Segoe UI,Roboto,Helvetica,Arial,'PingFang SC','Noto Sans SC','Microsoft YaHei',sans-serif;background:#0b1220;color:#e6e6e6;margin:0}}
.box{{max-width:440px;margin:12vh auto;padding:28px 24px;background:#111827;border:1px solid #263041;border-radius:16px;box-shadow:0 6px 26px rgba(0,0,0,.4)}}
h1{{font-size:20px;margin:0 0 16px}}
label, input{{display:block;width:100%}}
input{{margin-top:8px;padding:12px 14px;border-radius:10px;border:1px solid #334155;background:#0b1320;color:#e6e6e6;outline:none}}
button{{margin-top:16px;width:100%;padding:12px 14px;border:0;border-radius:10px;background:#2563eb;color:#fff;cursor:pointer}}
.err{{margin-top:12px;color:#fca5a5;font-size:13px}}
.tip{{margin-top:8px;color:#9ca3af;font-size:12px}}
</style></head>
<body><div class="box">
<h1>Routine Manager 登录</h1>
<form method="post">
  <label>管理密钥
    <input name="key" type="password" placeholder="请输入 Bot 发给你的密钥" autofocus>
  </label>
  <button type="submit">进入</button>
  {"<div class='err'>"+error+"</div>" if error else ""}
  <div class="tip">密钥由管理员在聊天中执行“作息管理 开启管理后台”获得。</div>
</form>
</div></body></html>"""

# ---------- 登录守卫 ----------
@app.before_request
async def need_login():
    # 放行登录、静态资源、JSON API（API 自己返回 401 JSON）
    if request.endpoint in {"login", "assets", "api_load", "api_save", "api_config"}:
        return
    if not session.get("authenticated"):
        return redirect(url_for("login"))

# ---------- 登录 ----------
@app.route("/login", methods=["GET", "POST"])
async def login():
    # 已登录直接去 /app
    if session.get("authenticated"):
        return redirect(url_for("app_page"))

    # 处理提交
    if request.method == "POST":
        form = await request.form
        key = form.get("key", "")

        # 过期校验
        if KEY_EXPIRES_AT and time.time() > KEY_EXPIRES_AT:
            return Response(_inline_login_html("密钥已过期，请向管理员重新获取。"), mimetype="text/html")

        # 密钥匹配
        if SERVER_LOGIN_KEY and key == SERVER_LOGIN_KEY:
            session["authenticated"] = True
            # 一次性：首次登录即失效
            if ONE_TIME_KEY:
                # 置空并立刻过期
                globals()["SERVER_LOGIN_KEY"] = ""
                globals()["KEY_EXPIRES_AT"] = 0.0
            return redirect(url_for("app_page"))

        return Response(_inline_login_html("密钥错误，请重试。"), mimetype="text/html")

    # GET：有自定义模板用模板，否则内置
    login_tmpl = os.path.join(ASSETS_DIR, "login.html")
    if os.path.exists(login_tmpl):
        return await send_from_directory(ASSETS_DIR, "login.html")
    return Response(_inline_login_html(), mimetype="text/html")


# ---------- 前端周视图 ----------
@app.route("/")
async def root():
    return redirect(url_for("app_page"))

@app.route("/app")
async def app_page():
    # 自动兼容三种常见文件名
    for name in ("index.html", "weekly.html", "weekly_schedule_clean_title.html"):
        p = os.path.join(ASSETS_DIR, name)
        if os.path.exists(p):
            return await send_from_directory(ASSETS_DIR, name)
    return Response(
        "<h2 style='font-family:system-ui'>未找到前端文件：请把周视图 HTML 放到插件 assets/ 目录下（index.html / weekly.html / weekly_schedule_clean_title.html 其一）。</h2>",
        mimetype="text/html", status=500
    )

# ---------- 静态资源 ----------
@app.route("/assets/<path:path>")
async def assets(path):
    return await send_from_directory(ASSETS_DIR, path)

# ---------- 工具：events → weekly 映射 ----------
def _events_to_weekly(events):
    weekly = {k: {} for k in WEEK_KEYS}
    for ev in events or []:
        wd = ev.get("weekday", ev.get("day"))
        start = ev.get("startTime", ev.get("start"))
        end = ev.get("endTime", ev.get("end"))
        title = ev.get("title", ev.get("action", ev.get("name", "")))
        if wd is None or not start or not end:
            continue
        try:
            wd = int(wd)
            if 0 <= wd <= 6:
                def _fmt(s):
                    s = str(s).strip()
                    if len(s) == 5 and s[2] == ":": return s
                    if ":" in s:
                        hh, mm = s.split(":")[:2]
                        return f"{int(hh):02d}:{int(mm):02d}"
                    return s
                rng = f"{_fmt(start)}-{_fmt(end)}"
                sh, sm = map(int, rng.split("-")[0].split(":"))
                eh, em = map(int, rng.split("-")[1].split(":"))
                if (eh, em) <= (sh, sm):
                    continue
                weekly[WEEK_KEYS[wd]][rng] = str(title).strip()
        except Exception:
            continue
    return weekly

def _load_disk():
    if STORAGE_PATH and os.path.exists(STORAGE_PATH):
        try:
            with open(STORAGE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return dict(INITIAL_CONFIG or {})

def _save_disk(cfg: dict):
    if not STORAGE_PATH:
        return False
    try:
        with open(STORAGE_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False

# ---------- API：加载 ----------
@app.get("/api/load")
@app.get("/load")
async def api_load():
    if not session.get("authenticated"):
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    data = _load_disk()
    return jsonify({"ok": True, "data": data})

# ---------- API：一把梭配置（前端 saveToAstrBot 调的就是这个） ----------
@app.post("/api/config")
async def api_config():
    if not session.get("authenticated"):
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    try:
        payload = await request.get_json(force=True, silent=False)
    except Exception:
        return jsonify({"ok": False, "error": "invalid_json"}), 400

    # 预期结构：
    # {
    #   "timezone": "Asia/Shanghai",
    #   "inject_scope": "all",
    #   "schedule": { "Mon": {"07:00-08:00": "起床"}, ... },
    #   "prompt": { "routine_prompt_template": "现在时间：{now} 当前行为：{action} 请在语气和内容上贴合该场景进行回复。" }
    # }
    tz = (payload.get("timezone") or "Asia/Shanghai").strip()
    inject_scope = (payload.get("inject_scope") or "all").strip()
    weekly = payload.get("schedule") or {}
    prompt = payload.get("prompt") or {}
    if not isinstance(weekly, dict):
        return jsonify({"ok": False, "error": "bad_schedule"}), 400

    # 合并/落盘
    cfg = _load_disk()
    cfg["timezone"] = tz
    cfg["inject_scope"] = inject_scope
    # 只保留我们要用的这一条模板
    tpl = prompt.get("routine_prompt_template") or "现在时间：{now} 当前行为：{action} 请在语气和内容上贴合该场景进行回复。"
    cfg["prompt"] = {"routine_prompt_template": tpl}
    cfg["schedule"] = {k: dict(v) for k, v in weekly.items() if isinstance(v, dict)}

    if _save_disk(cfg):
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "save_failed"}), 500


# ---------- API：保存 ----------
@app.post("/api/save")
@app.post("/save")
async def api_save():
    if not session.get("authenticated"):
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    try:
        payload = await request.get_json(force=True, silent=False)
    except Exception:
        return jsonify({"ok": False, "error": "invalid_json"}), 400

    weekly = None
    if isinstance(payload, dict):
        if "events" in payload:
            weekly = _events_to_weekly(payload.get("events") or [])
        elif "schedule" in payload:
            sch = payload.get("schedule") or {}
            if isinstance(sch, dict):
                weekly = {k: dict(v) for k, v in sch.items() if isinstance(v, dict)}
    if weekly is None:
        return jsonify({"ok": False, "error": "missing_schedule"}), 400

    cfg = _load_disk()
    cfg.setdefault("timezone", "Asia/Shanghai")
    cfg.setdefault("inject_scope", "all")
    cfg.setdefault("prompt", {"routine_prompt_template": "现在时间：{now} 当前行为：{action} 请在语气和内容上贴合该场景进行回复。"})
    cfg["schedule"] = weekly

    if _save_disk(cfg):
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "save_failed"}), 500

# ---------- 启动 ----------
def run_server(cfg: dict):
    asyncio.run(start_server(cfg))

async def start_server(cfg: dict):
    global SERVER_LOGIN_KEY, STORAGE_PATH, INITIAL_CONFIG, ONE_TIME_KEY, KEY_EXPIRES_AT
    SERVER_LOGIN_KEY = cfg.get("server_key") or ""
    STORAGE_PATH = cfg.get("storage_path") or os.path.join(os.path.dirname(__file__), "routine_config.json")
    INITIAL_CONFIG = cfg.get("plugin_config") or {}

    # 一次性密钥 + 过期
    ONE_TIME_KEY = bool(cfg.get("one_time_key", True))
    ttl = int(cfg.get("key_ttl_seconds", 600))
    KEY_EXPIRES_AT = time.time() + ttl if SERVER_LOGIN_KEY else 0.0

    app.secret_key = os.urandom(16)
    os.makedirs(ASSETS_DIR, exist_ok=True)

    port = int(cfg.get("webui_port", 58101))
    host = str(cfg.get("host", "0.0.0.0"))
    hc = Config()
    hc.bind = [f"{host}:{port}"]
    hc.graceful_timeout = 5
    await hypercorn.asyncio.serve(app, hc)
