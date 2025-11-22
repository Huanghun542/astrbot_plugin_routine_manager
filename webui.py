import os
import json
import time
import asyncio
from quart import Quart, request, redirect, url_for, session, send_from_directory, Response, jsonify
import hypercorn.asyncio
from hypercorn.config import Config

# 初始化 Quart 应用
app = Quart(__name__)

# 全局变量（由 main.py 启动时注入）
SERVER_LOGIN_KEY = ""          
STORAGE_PATH = None            
INITIAL_CONFIG = {}            
ONE_TIME_KEY = True            
KEY_EXPIRES_AT = 0.0           

# 常量
ASSETS_DIR = os.path.join(os.path.dirname(__file__), "assets")
WEEK_KEYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# ==================== 辅助函数 ====================

def _load_disk_config() -> dict:
    """读取磁盘配置，若失败则返回内存中的初始配置"""
    if STORAGE_PATH and os.path.exists(STORAGE_PATH):
        try:
            with open(STORAGE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return dict(INITIAL_CONFIG or {})

def _save_disk_config(cfg: dict) -> bool:
    """写入磁盘配置"""
    if not STORAGE_PATH:
        return False
    try:
        with open(STORAGE_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False

def _render_login_html(error: str = "") -> str:
    """内置极简登录页渲染（当 assets/login.html 缺失时使用）"""
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Routine Manager</title>
<style>body{{background:#f3f4f6;font-family:system-ui;display:flex;justify-content:center;align-items:center;height:100vh;margin:0}}
.card{{background:white;padding:2rem;border-radius:1rem;box-shadow:0 10px 15px -3px rgba(0,0,0,0.1);width:100%;max-width:400px}}
input{{width:100%;padding:0.75rem;border:1px solid #e5e7eb;border-radius:0.5rem;margin-bottom:1rem;box-sizing:border-box}}
button{{width:100%;background:#2563eb;color:white;padding:0.75rem;border:none;border-radius:0.5rem;cursor:pointer;font-weight:bold}}
button:hover{{background:#1d4ed8}}.err{{color:#ef4444;font-size:0.875rem;margin-bottom:1rem;text-align:center}}</style>
</head><body><div class="card">
<h2 style="margin-top:0;color:#1f2937;text-align:center">系统登录</h2>
{f'<div class="err">{error}</div>' if error else ''}
<form method="post"><input type="password" name="key" placeholder="输入临时密钥" required autofocus>
<button type="submit">验证身份</button></form></div></body></html>"""

# ==================== 路由处理 ====================

@app.before_request
async def login_guard():
    """全局登录守卫"""
    # 白名单路由
    if request.endpoint in {"login", "serve_assets", "api_check_status"}:
        return
    # 静态资源若在 assets 下也放行（视具体需求，通常建议保护）
    if request.path.startswith("/assets/"):
        return
        
    if not session.get("authenticated"):
        # API 请求返回 401
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "error": "unauthorized"}), 401
        # 页面请求重定向到登录
        return redirect(url_for("login"))

@app.route("/login", methods=["GET", "POST"])
async def login():
    if session.get("authenticated"):
        return redirect(url_for("index"))

    if request.method == "POST":
        form = await request.form
        user_key = form.get("key", "")

        # 1. 校验过期
        if KEY_EXPIRES_AT and time.time() > KEY_EXPIRES_AT:
            return Response(_render_login_html("密钥已失效，请在 AstrBot 重新生成"), mimetype="text/html")

        # 2. 校验密钥
        if SERVER_LOGIN_KEY and user_key == SERVER_LOGIN_KEY:
            session["authenticated"] = True
            # 一次性密钥：登录成功后立即销毁服务器端的 Key
            if ONE_TIME_KEY:
                globals()["SERVER_LOGIN_KEY"] = ""
                globals()["KEY_EXPIRES_AT"] = 0.0
            return redirect(url_for("index"))
        
        return Response(_render_login_html("密钥错误"), mimetype="text/html")

    # 尝试加载自定义登录页
    custom_login = os.path.join(ASSETS_DIR, "login.html")
    if os.path.exists(custom_login):
        return await send_from_directory(ASSETS_DIR, "login.html")
    return Response(_render_login_html(), mimetype="text/html")

@app.route("/")
async def index():
    """主页：优先查找 index.html"""
    for name in ["index.html", "weekly.html"]:
        if os.path.exists(os.path.join(ASSETS_DIR, name)):
            return await send_from_directory(ASSETS_DIR, name)
    return "<h1>404 Error</h1><p>未找到前端文件 (assets/index.html)，请检查插件安装完整性。</p>", 404

@app.route("/assets/<path:filename>")
async def serve_assets(filename):
    return await send_from_directory(ASSETS_DIR, filename)

# ==================== API 接口 ====================

@app.get("/api/load")
async def api_load():
    """获取当前配置（供前端初始化数据）"""
    data = _load_disk_config()
    # 确保返回前端需要的基本结构，防止前端报错
    data.setdefault("timezone", "Asia/Shanghai")
    data.setdefault("inject_scope", "all")
    data.setdefault("schedule", {})
    return jsonify({"ok": True, "data": data})

@app.post("/api/config")
async def api_config():
    """保存完整配置（前端 saveToAstrBot 调用）"""
    try:
        payload = await request.get_json()
    except Exception:
        return jsonify({"ok": False, "error": "invalid_json"}), 400

    # 1. 提取并清洗数据
    tz = str(payload.get("timezone") or "Asia/Shanghai").strip()
    scope = str(payload.get("inject_scope") or "all").strip()
    
    # 提示词模板处理
    prompt_conf = payload.get("prompt") or {}
    tpl = prompt_conf.get("routine_prompt_template", "").strip()
    if not tpl:
        tpl = "现在时间：{now} 当前行为：{action} 请在语气和内容上贴合该场景进行回复。"

    # 日程表处理（确保是 Dict[str, Dict]）
    raw_schedule = payload.get("schedule") or {}
    clean_schedule = {}
    if isinstance(raw_schedule, dict):
        for day_key in WEEK_KEYS:
            day_data = raw_schedule.get(day_key) or {}
            if isinstance(day_data, dict):
                # 过滤空的时间段（可选，或者信任前端）
                clean_schedule[day_key] = {k: str(v) for k, v in day_data.items() if v}

    # 2. 组装新配置
    new_config = {
        "timezone": tz,
        "inject_scope": scope,
        "webui_port": INITIAL_CONFIG.get("webui_port", 58101), # 端口保留原配置
        "prompt": {
            "routine_prompt_template": tpl
        },
        "schedule": clean_schedule
    }

    # 3. 落盘
    if _save_disk_config(new_config):
        return jsonify({"ok": True})
    else:
        return jsonify({"ok": False, "error": "write_disk_failed"}), 500

# ==================== 启动逻辑 ====================

async def start_server(cfg: dict):
    global SERVER_LOGIN_KEY, STORAGE_PATH, INITIAL_CONFIG, ONE_TIME_KEY, KEY_EXPIRES_AT
    
    # 从 main.py 传入的参数初始化
    SERVER_LOGIN_KEY = cfg.get("server_key", "")
    STORAGE_PATH = cfg.get("storage_path")
    INITIAL_CONFIG = cfg.get("plugin_config", {})
    ONE_TIME_KEY = cfg.get("one_time_key", True)
    
    # 设置过期时间
    ttl = int(cfg.get("key_ttl_seconds", 600))
    KEY_EXPIRES_AT = time.time() + ttl if SERVER_LOGIN_KEY else 0.0

    # 配置 App
    app.secret_key = os.urandom(24)  # Session 密钥
    
    # 确保 assets 目录存在
    os.makedirs(ASSETS_DIR, exist_ok=True)

    # Hypercorn 配置
    port = int(cfg.get("webui_port", 58101))
    host = str(cfg.get("host", "0.0.0.0"))
    
    hc_cfg = Config()
    hc_cfg.bind = [f"{host}:{port}"]
    hc_cfg.graceful_timeout = 2
    hc_cfg.worker_class = "asyncio"
    
    await hypercorn.asyncio.serve(app, hc_cfg)

def run_server(cfg: dict):
    """入口函数，由 multiprocess 调用"""
    asyncio.run(start_server(cfg))