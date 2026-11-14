"""Telegram Public Channel Reader — Flask app for Liara
صفحه عمومی برای نمایش محتوا + صفحه ادمین محافظت‌شده با رمز برای تنظیم متغیرها."""
import os
import json
import socket
import threading
import time

try:  # fcntl فقط روی لینوکس است؛ روی ویندوز قفل ساده کافی است
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

import requests
from flask import (Flask, render_template, request, session,
                   redirect, url_for, jsonify, flash, send_from_directory, abort)

from store import Store
import scraper
import tg_client
import publisher

# ---------- متغیرهای محیطی (در پنل Railway/لیارا تنظیم می‌شوند) ----------
# روی Railway یک Volume بچسبانید (مثلاً /data) و DATA_DIR را همان قرار دهید؛
# پیش‌فرض پوشه data کنار پروژه است تا اجرای محلی هم بدون تنظیم کار کند.
DATA_DIR        = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
ADMIN_PASSWORD  = os.environ.get("ADMIN_PASSWORD", "admin123")   # حتماً تغییر دهید
SECRET_KEY      = os.environ.get("SECRET_KEY", "change-me-please")
API_ID_ENV      = os.environ.get("API_ID", "")
API_HASH_ENV    = os.environ.get("API_HASH", "")
TG_SESSION_ENV  = os.environ.get("TG_SESSION", "")  # اختیاری: سشن آماده از محلی

app = Flask(__name__)
app.secret_key = SECRET_KEY
store = Store(DATA_DIR)


# ---------- مسیر اتصال به تلگرام (رله اختیاری) ----------
_orig_getaddrinfo = socket.getaddrinfo


def apply_network_settings():
    """اعمال تنظیمات رله و IPv4 از تنظیمات ذخیره‌شده، در ماژول tg_client و شبکه سیستم.
    اگر پنل حالت none باشد ولی متغیر محیطی TG_PROXY ست شده باشد، از آن استفاده می‌شود."""
    s = store.get_settings()
    mode = s.get("relay_mode", "none")
    proxy = s.get("relay_proxy", "")
    base_url = s.get("relay_base_url", "")
    if mode == "none":
        env_proxy = (os.environ.get("TG_PROXY") or "").strip()
        if env_proxy:
            mode, proxy = "proxy", env_proxy
    tg_client.configure_relay(mode=mode, proxy=proxy, base_url=base_url)
    force_v4 = bool(s.get("force_ipv4"))
    if force_v4 and not getattr(socket, "_tg_force_v4_applied", False):
        def _v4_only(*args, **kwargs):
            return [r for r in _orig_getaddrinfo(*args, **kwargs)
                    if r[0] == socket.AF_INET]
        socket.getaddrinfo = _v4_only
        socket._tg_force_v4_applied = True
    print(f"[net] relay_mode={s.get('relay_mode', 'none')} force_ipv4={force_v4}",
          flush=True)


apply_network_settings()


# ---------- پردازش هوشمند آرشیو ----------
AI_API_BASE_URL = (os.environ.get("AI_API_BASE_URL") or "").strip().rstrip("/")
AI_API_KEY = (os.environ.get("AI_API_KEY") or "").strip()
AI_MODEL = (os.environ.get("AI_MODEL") or "").strip()


def enrich_message(msg: dict, channel: str) -> dict:
    """عنوان و دسته‌بندی قابل استفاده در آرشیو؛ در صورت تنظیم API از مدل سازگار با OpenAI کمک می‌گیرد."""
    filename = msg.get("media_name") or ""
    text_body = (msg.get("text") or "").strip()
    fallback_title = filename or (text_body[:90] if text_body else
                                  {"photo": "تصویر تلگرام", "video": "ویدئو تلگرام",
                                   "audio": "فایل صوتی تلگرام", "document": "فایل تلگرام"}.get(
                                       msg.get("has_media"), "پیام تلگرام"))
    msg.setdefault("title", fallback_title)
    msg.setdefault("description", text_body)
    msg.setdefault("category", guess_category(filename, text_body, msg.get("has_media")))
    msg.setdefault("tags", [])
    if not (AI_API_BASE_URL and AI_API_KEY and AI_MODEL):
        msg["ai_status"] = "fallback"
        return msg
    try:
        prompt = (
            "برای آرشیو فایل‌های یک کانال تلگرام، از اطلاعات زیر متادیتا بساز. "
            "فقط JSON معتبر با کلیدهای title (عنوان فارسی کوتاه و دقیق)، "
            "description (توضیح فارسی کوتاه)، category (یکی از مدل سه‌بعدی، "
            "تصویر، ویدئو، صوت، سند، نرم‌افزار، آرشیو، سایر) و tags (آرایه رشته‌ای فارسی) برگردان. "
            "چیزی را که از داده‌ها معلوم نیست جعل نکن.\n"
            + json.dumps({"channel": channel, "message": text_body,
                          "filename": filename, "media_type": msg.get("has_media")},
                         ensure_ascii=False)
        )
        response = requests.post(
            AI_API_BASE_URL + "/chat/completions",
            headers={"Authorization": "Bearer " + AI_API_KEY,
                     "Content-Type": "application/json"},
            json={"model": AI_MODEL, "temperature": 0.2,
                  "messages": [{"role": "system", "content": "Return only valid JSON."},
                               {"role": "user", "content": prompt}]},
            timeout=35)
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        content = content.strip()
        if content.startswith("```"):  # حذف محصورکننده مارک‌داون از پاسخ مدل
            content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        data = json.loads(content)
        if data.get("title"): msg["title"] = str(data["title"])[:200]
        if data.get("description"): msg["description"] = str(data["description"])[:1500]
        if data.get("category"): msg["category"] = str(data["category"])[:80]
        if isinstance(data.get("tags"), list): msg["tags"] = [str(x)[:50] for x in data["tags"][:12]]
        msg["ai_status"] = "generated"
    except Exception as e:
        # دریافت پیام باید حتی در صورت اختلال سرویس AI ادامه پیدا کند.
        msg["ai_status"] = "fallback"
        msg["ai_error"] = str(e)[:250]
    return msg


def guess_category(filename: str, text_body: str, media_type: str) -> str:
    value = (filename + " " + text_body).lower()
    ext = os.path.splitext(filename)[1].lower()
    if ext in {".stl", ".3mf", ".obj", ".step", ".stp", ".gcode", ".amf"} or any(
            x in value for x in ["پرینت سه بعدی", "مدل سه بعدی", "3d model"]):
        return "مدل سه‌بعدی"
    if ext in {".zip", ".rar", ".7z", ".tar", ".gz"}: return "آرشیو"
    if ext in {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt"}: return "سند"
    if ext in {".exe", ".msi", ".apk", ".deb", ".dmg"}: return "نرم‌افزار"
    if media_type == "photo" or ext in {".jpg", ".jpeg", ".png", ".webp", ".gif"}: return "تصویر"
    if media_type == "video" or ext in {".mp4", ".mov", ".mkv", ".webm"}: return "ویدئو"
    if media_type == "audio" or ext in {".mp3", ".wav", ".ogg", ".m4a"}: return "صوت"
    if media_type == "document": return "فایل"
    return "سایر"


# ---------- منطق دریافت پیام‌ها ----------
# رسانه‌ها قبل از انتشار در تمپ دانلود می‌شوند تا دیسک دائمی Railway (۵۰۰MB) پر نشود.
MEDIA_TMP_ROOT = os.environ.get("MEDIA_TMP_ROOT", "/tmp/tg-media")


def do_fetch(channel: str) -> dict:
    """یک بار دریافت پیام‌های جدید یک کانال و ذخیره (افزایشی، بر اساس min_id).
    اگر انتشار (endpoint/FTP) تنظیم شده باشد، هر پیام به cPanel منتشر می‌شود و
    فایل رسانه از تمپ حذف می‌شود؛ در غیر این صورت مثل قبل محلی نگه داشته می‌شود."""
    s = store.get_settings()
    engine = s.get("engine", "telethon")
    pub_cfg = publisher.publish_config()
    do_publish = publisher.publish_enabled(pub_cfg)
    # وقتی انتشار فعال است، رسانه به تمپ می‌رود؛ وقتی نه، به دیسک دائمی
    media_root = MEDIA_TMP_ROOT if do_publish else os.path.join(DATA_DIR, "media")
    try:
        if engine == "telethon":
            api_id = int(API_ID_ENV or s.get("api_id", "") or 0)
            api_hash = API_HASH_ENV or s.get("api_hash", "")
            sess = TG_SESSION_ENV or s.get("tg_session", "")
            if not (api_id and api_hash and sess):
                return {"ok": False, "new": 0,
                        "error": "برای موتور telethon باید API_ID و API_HASH و "
                                 "سشن ورود (از بخش ورود تلگرام پایین همین صفحه) تنظیم شده باشد."}
            res = tg_client.fetch_messages(
                api_id, api_hash, sess, channel,
                limit=s.get("history_limit", 100),
                min_id=store.latest_id(channel) or 0,
                media_root=media_root)
            if not res.get("ok"):
                return {"ok": False, "new": 0, "error": res.get("error", "خطای ناشناخته")}
            msgs = [enrich_message(m, channel) for m in res["messages"]]
            store.upsert_messages(channel, msgs)
            published, ftp_errors = 0, 0
            if do_publish:
                for m in msgs:
                    try:
                        pr = publisher.publish_message(m, pub_cfg, media_root=media_root)
                        if pr.get("ok"):
                            published += 1
                        if str(pr.get("ftp", "")).startswith("error"):
                            ftp_errors += 1
                        store.upsert_messages(channel, [m])  # مسیر/URL نهایی رسانه ذخیره شود
                    except Exception as pe:
                        print(f"[publish] @{channel}#{m.get('id')}: {pe}", flush=True)
            return {"ok": True, "new": len(msgs), "title": res.get("title", ""),
                    "downloaded": sum(1 for m in msgs if m.get("media_path")),
                    "published": published, "ftp_errors": ftp_errors}
        else:  # web
            msgs = scraper.fetch_new(channel,
                                     max_pages=s.get("web_max_pages", 5),
                                     min_id=store.latest_id(channel) or 0)
            msgs = [enrich_message(m, channel) for m in msgs]
            store.upsert_messages(channel, msgs)
            published = 0
            if do_publish:
                for m in msgs:
                    try:
                        if publisher.publish_message(m, pub_cfg, media_root=media_root).get("ok"):
                            published += 1
                        store.upsert_messages(channel, [m])
                    except Exception as pe:
                        print(f"[publish] @{channel}#{m.get('id')}: {pe}", flush=True)
            return {"ok": True, "new": len(msgs), "downloaded": 0, "published": published}
    except Exception as e:
        return {"ok": False, "new": 0, "error": str(e)}


def _worker_loop():
    """حلقه پس‌زمینه: هر refresh_interval ثانیه همه کانال‌ها را چک می‌کند."""
    while True:
        s = store.get_settings()
        if s.get("live_listen", True):
            for ch in s.get("channels", []):
                r = do_fetch(ch)
                print(f"[worker] @{ch}: new={r.get('new')} err={r.get('error', '-')}",
                      flush=True)
        time.sleep(max(15, int(s.get("refresh_interval", 60))))


def _start_worker():
    # اگر چند instance اجرا شود، با قفل فایل فقط یکی حلقه را اجرا می‌کند.
    # fcntl روی لینوکس هست؛ روی ویندوز (اجرای محلی) از O_EXCL استفاده می‌کنیم.
    lock_path = os.path.join(DATA_DIR, "worker.lock")
    try:
        if fcntl is not None:
            fh = open(lock_path, "w")
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        else:
            if os.path.exists(lock_path):
                # قفل قدیمی از اجرای قبلی؛ اگر پروسه‌ای زنده آن را نگه داشته باشد
                # ساخت دوباره شکست می‌خورد.
                try:
                    os.remove(lock_path)
                except OSError:
                    print("[worker] another instance holds the lock; skipping", flush=True)
                    return
            open(lock_path, "x").close()
        t = threading.Thread(target=_worker_loop, daemon=True)
        t.start()
        print("[worker] background loop started", flush=True)
    except OSError:
        print("[worker] another instance holds the lock; skipping", flush=True)


if os.environ.get("WORKER_ENABLED", "1") != "0":
    _start_worker()


# ---------- احراز هویت ادمین ----------
def is_admin():
    return session.get("admin") is True


def require_admin():
    if not is_admin():
        return redirect(url_for("admin_login"))
    return None


# ---------- تست اتصال به تلگرام (سمت سرور اجرا می‌شود) ----------
TELEGRAM_DC_HOST = "api.telegram.org"  # هم MTProto و هم Bot API از این دامنه استفاده می‌کنند


def telegram_connectivity_check() -> dict:
    """بررسی دسترسی سرور به تلگرام: ۱) TCP به سرورهای تلگرام  ۲) HTTPS به t.me.
    هر دو از داخل همین سرور اجرا می‌شوند؛ نتیجه یعنی سرور فیلتر است یا نه."""
    checks, ok_all = [], True

    # ۱) اتصال TCP به پورت 443 — در حالت پروکسی، خودِ پروکسی تست می‌شود
    target = tg_client.proxy_conn_target() or (TELEGRAM_DC_HOST, 443)
    t0 = time.time()
    try:
        with socket.create_connection(target, timeout=8):
            latency = round((time.time() - t0) * 1000)
        checks.append({"name": f"اتصال TCP به {target[0]}:{target[1]}",
                       "ok": True, "detail": f"موفق — تأخیر {latency} میلی‌ثانیه"})
    except Exception as e:
        ok_all = False
        checks.append({"name": f"اتصال TCP به {target[0]}:{target[1]}",
                       "ok": False, "detail": f"ناموفق — {e}"})

    # ۲) در صورت تنظیم بودن BOT_TOKEN، فراخوانی واقعی getMe (روش بات)
    bot_token = (os.environ.get("BOT_TOKEN") or "").strip()
    if bot_token:
        t0 = time.time()
        try:
            r = requests.get(f"https://api.telegram.org/bot{bot_token}/getMe", timeout=10,
                             proxies=tg_client.proxies_for_requests())
            latency = round((time.time() - t0) * 1000)
            data = r.json()
            if data.get("ok"):
                bot_name = data["result"].get("username", "")
                checks.append({"name": "فراخوانی getMe با BOT_TOKEN",
                               "ok": True, "detail": f"موفق — ربات @{bot_name}، تأخیر {latency} میلی‌ثانیه"})
            else:
                ok_all = False
                checks.append({"name": "فراخوانی getMe با BOT_TOKEN",
                               "ok": False, "detail": f"تلگرام خطا برگرداند: {data.get('description', r.status_code)}"})
        except Exception as e:
            ok_all = False
            checks.append({"name": "فراخوانی getMe با BOT_TOKEN",
                           "ok": False, "detail": f"ناموفق — {e}"})

    # ۳) بازکردن پیش‌نمایش وب تلگرام — در حالت base_url، رله سفارشی تست می‌شود
    web_base = tg_client.telegram_web_base()
    t0 = time.time()
    try:
        r = requests.get(web_base + "/telegram", timeout=10,
                         headers={"User-Agent": "Mozilla/5.0"},
                         proxies=tg_client.proxies_for_requests())
        latency = round((time.time() - t0) * 1000)
        if r.status_code == 200:
            checks.append({"name": f"دسترسی HTTPS به پیش‌نمایش وب ({web_base})",
                           "ok": True, "detail": f"موفق — کد {r.status_code}، تأخیر {latency} میلی‌ثانیه"})
        else:
            ok_all = False
            checks.append({"name": f"دسترسی HTTPS به پیش‌نمایش وب ({web_base})",
                           "ok": False, "detail": f"کد وضعیت غیرمنتظره: {r.status_code} — آدرس رله درست است؟"})
    except Exception as e:
        ok_all = False
        checks.append({"name": f"دسترسی HTTPS به پیش‌نمایش وب ({web_base})",
                       "ok": False, "detail": f"ناموفق — {e}"})

    return {"ok": ok_all, "checks": checks,
            "time": time.strftime("%Y-%m-%d %H:%M:%S")}


@app.route("/admin/telegram-test")
def tg_test():
    r = require_admin()
    if r:
        return r
    return jsonify(telegram_connectivity_check())


# ---------- عیب‌یابی کامل شبکه (سمت سرور اجرا می‌شود) ----------
def _dns_records(host: str) -> dict:
    v4, v6 = [], []
    try:
        for fam, _st, _proto, _cn, sa in socket.getaddrinfo(host, 443):
            if fam == socket.AF_INET and sa[0] not in v4:
                v4.append(sa[0])
            elif fam == socket.AF_INET6 and sa[0] not in v6:
                v6.append(sa[0])
    except Exception as e:
        return {"ok": False, "detail": str(e), "v4": [], "v6": []}
    return {"ok": bool(v4 or v6), "v4": v4, "v6": v6,
            "detail": f"{len(v4)} رکورد IPv4، {len(v6)} رکورد IPv6"}


def _tcp_probe(host: str, port: int = 443, timeout: float = 8.0) -> dict:
    """اتصال TCP خام؛ family=0 یعنی هر خانواده، 4 و 6 فقط همان نسخه."""
    t0 = time.time()
    last_err = None
    try:
        infos = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
    except Exception as e:
        return {"ok": False, "detail": f"خطای DNS — {e}"}
    for fam, _st, _proto, _cn, sa in infos:
        sock = socket.socket(fam, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect(sa[:2] if fam == socket.AF_INET else sa)
            sock.close()
            latency = round((time.time() - t0) * 1000)
            ver = "IPv4" if fam == socket.AF_INET else "IPv6"
            return {"ok": True, "detail": f"موفق از {ver} ({sa[0]}) — تأخیر {latency} میلی‌ثانیه"}
        except Exception as e:
            last_err = e
            ver = "IPv4" if fam == socket.AF_INET else "IPv6"
            last_err = f"{ver} ({sa[0]}): {e}"
        finally:
            try:
                sock.close()
            except Exception:
                pass
    return {"ok": False, "detail": f"ناموفق — {last_err}"}


def network_diagnosis() -> dict:
    """عیب‌یابی گام‌به‌گام: DNS، اینترنت عمومی (میزبان خنثی)، و تلگرام —
    تا مشخص شود کل خروجی سرور قطع است یا فقط دسترسی به تلگرام بسته شده."""
    checks = []

    # ۱) DNS
    dns_tg = _dns_records("api.telegram.org")
    checks.append({"name": "DNS — resolve کردن api.telegram.org",
                   "ok": dns_tg["ok"],
                   "detail": ("موفق — " + dns_tg["detail"] +
                              (" | IPv4: " + ", ".join(dns_tg["v4"][:3]) if dns_tg["v4"] else "") +
                              (" | IPv6: " + ", ".join(dns_tg["v6"][:3]) if dns_tg["v6"] else ""))
                   if dns_tg["ok"] else f"ناموفق — {dns_tg['detail']}"})

    dns_n = _dns_records("example.com")
    checks.append({"name": "DNS — resolve کردن example.com (میزبان خنثی)",
                   "ok": dns_n["ok"],
                   "detail": ("موفق — " + dns_n["detail"]) if dns_n["ok"]
                   else f"ناموفق — {dns_n['detail']}"})

    # ۲) اینترنت عمومی با میزبان خنثی — اگر این کار کند، خروجی اینترنت سرور سالم است
    pub = _tcp_probe("example.com", 443)
    checks.append({"name": "اینترنت عمومی — اتصال TCP به example.com:443",
                   "ok": pub["ok"], "detail": pub["detail"]})

    # ۳) تلگرام مستقیم
    tg = _tcp_probe("api.telegram.org", 443)
    checks.append({"name": "تلگرام — اتصال TCP به api.telegram.org:443 (مستقیم)",
                   "ok": tg["ok"], "detail": tg["detail"]})

    # ۴) HTTPS واقعی به تلگرام
    t0 = time.time()
    try:
        r = requests.get("https://api.telegram.org", timeout=10,
                         headers={"User-Agent": "Mozilla/5.0"})
        latency = round((time.time() - t0) * 1000)
        ok = r.status_code < 500
        checks.append({"name": "تلگرام — HTTPS به api.telegram.org",
                       "ok": ok,
                       "detail": (f"پاسخ گرفته شد — کد {r.status_code}، تأخیر {latency} میلی‌ثانیه") if ok
                       else f"سرور تلگرام پاسخ خطا داد — کد {r.status_code}"})
    except Exception as e:
        checks.append({"name": "تلگرام — HTTPS به api.telegram.org",
                       "ok": False, "detail": f"ناموفق — {e}"})

    # ۵) IP خروجی سرور
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 53))
        egress = s.getsockname()[0]
        s.close()
        checks.append({"name": "IP خروجی سرور (سمت اینترنت)",
                       "ok": True, "detail": egress})
    except Exception as e:
        checks.append({"name": "IP خروجی سرور (سمت اینترنت)",
                       "ok": False, "detail": str(e)})

    ok_all = all(c["ok"] for c in checks)
    if pub["ok"] and not tg["ok"]:
        verdict = ("🔎 نتیجه: اینترنت سرور سالم است ولی دسترسی به تلگرام بسته شده "
                   "(بلاک سطح دیتاسنتر/ISP). باید از رله (پروکسی یا آدرس پایه سفارشی) استفاده کنید.")
    elif not pub["ok"]:
        verdict = ("🔎 نتیجه: خروجی اینترنت سرور به‌طور کلی مشکل دارد "
                   "(حتی میزبان خنثی هم باز نشد) — با پشتیبانی هاست تماس بگیرید.")
    else:
        verdict = "🔎 نتیجه: مسیر اتصال سالم است."
    checks.append({"name": verdict, "ok": True, "detail": ""})

    return {"ok": ok_all, "checks": checks,
            "time": time.strftime("%Y-%m-%d %H:%M:%S")}


@app.route("/admin/diagnose")
def admin_diagnose():
    r = require_admin()
    if r:
        return r
    return jsonify(network_diagnosis())


# ---------- صفحات عمومی ----------
@app.route("/")
def index():
    s = store.get_settings()
    channels = s.get("channels", [])
    active = request.args.get("ch", channels[0] if channels else "")
    limit = int(s.get("display_limit", 50))
    msgs = store.get_messages(active, limit) if active else []
    counts = {ch: store.count(ch) for ch in channels}
    return render_template("index.html", settings=s, channels=channels,
                           active=active, messages=msgs, counts=counts)


@app.route("/media/<path:filename>")
def media_file(filename):
    """عرض الصور وتنزيل الملفات المؤرشفة من مجلد الوسائط."""
    media_root = os.path.realpath(os.path.join(DATA_DIR, "media"))
    target = os.path.realpath(os.path.join(media_root, filename))
    if not target.startswith(media_root + os.sep) or not os.path.isfile(target):
        abort(404)
    return send_from_directory(media_root, filename, as_attachment=request.args.get("download") == "1")


@app.route("/api/messages/<channel>")
def api_messages(channel):
    limit = int(request.args.get("limit", 50))
    return jsonify({"channel": channel,
                    "messages": store.get_messages(channel, limit)})


# ---------- ورود ادمین ----------
@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        if request.form.get("password", "") == ADMIN_PASSWORD:
            session["admin"] = True
            return redirect(url_for("admin"))
        flash("رمز اشتباه است.")
    return render_template("login.html")


@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("index"))


# ---------- صفحه ادمین ----------
@app.route("/admin")
def admin():
    r = require_admin()
    if r:
        return r
    s = store.get_settings()
    counts = {ch: store.count(ch) for ch in s.get("channels", [])}
    result = None
    raw = request.args.get("result", "")
    if raw.startswith("fetch_ok="):
        try:
            result = {"ok": True, **json.loads(raw[9:])}
        except Exception:
            result = {"ok": True}
    elif raw.startswith("fetch_err="):
        result = {"ok": False, "error": raw[10:]}
    return render_template("admin.html", settings=s, counts=counts,
                           api_id_env=API_ID_ENV or s.get("api_id", ""), api_hash_env=API_HASH_ENV or s.get("api_hash", ""), api_hash_set=bool(API_HASH_ENV or s.get("api_hash", "")),
                           result=result, has_session=bool(s.get("tg_session") or TG_SESSION_ENV),
                           relay=tg_client.relay_settings())


@app.route("/admin/save", methods=["POST"])
def admin_save():
    r = require_admin()
    if r:
        return r
    s = {
        "engine": request.form.get("engine", "telethon"),
        "channels": [c.strip().lstrip("@") for c in
                     request.form.get("channels", "").splitlines() if c.strip()],
        "history_limit": int(request.form.get("history_limit", 100) or 100),
        "web_max_pages": int(request.form.get("web_max_pages", 5) or 5),
        "refresh_interval": int(request.form.get("refresh_interval", 60) or 60),
        "max_stored": int(request.form.get("max_stored", 2000) or 2000),
        "display_limit": int(request.form.get("display_limit", 50) or 50),
        "live_listen": request.form.get("live_listen") == "on",
    }
    store.save_settings(s)
    flash("تنظیمات ذخیره شد ✓")
    return redirect(url_for("admin"))


# ---------- وضعیت کار دریافت پس‌زمینه (دریافت دستی به‌صورت async) ----------
_job_lock = threading.Lock()
_job_state = {"running": False, "channel": "", "result": None, "started": 0.0}


def _run_fetch_job(channel: str):
    """اجرای دریافت در thread جدا؛ نتیجه در _job_state ذخیره می‌شود.
    دلیل: دریافت تاریخچه + دانلود رسانه + انتشار FTP ممکن است دقیقه‌ها طول
    بکشد و درون درخواست HTTP باعث timeout گنجورکورن می‌شود."""
    global _job_state
    try:
        res = do_fetch(channel)
    except Exception as e:
        res = {"ok": False, "new": 0, "error": str(e)}
    with _job_lock:
        _job_state = {"running": False, "channel": channel,
                      "result": res, "started": _job_state.get("started", 0.0)}


@app.route("/admin/fetch", methods=["POST"])
def admin_fetch():
    r = require_admin()
    if r:
        return r
    global _job_state
    channel = request.form.get("channel", "").strip().lstrip("@")
    with _job_lock:
        if _job_state.get("running"):
            return redirect(url_for("admin", result="fetch_err=یک دریافت در حال اجراست؛ کمی صبر کنید."))
        _job_state = {"running": True, "channel": channel, "result": None,
                      "started": time.time()}
    threading.Thread(target=_run_fetch_job, args=(channel,), daemon=True).start()
    flash(f"دریافت @{channel} در پس‌زمینه شروع شد؛ نتیجه را از دکمه «وضعیت کار» ببینید.")
    return redirect(url_for("admin"))


@app.route("/admin/job-status")
def admin_job_status():
    r = require_admin()
    if r:
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    with _job_lock:
        st = dict(_job_state)
    if st.get("running"):
        elapsed = round(time.time() - st.get("started", time.time()))
        return jsonify({"running": True, "channel": st.get("channel"),
                        "elapsed": elapsed})
    return jsonify({"running": False, "channel": st.get("channel"),
                    "result": st.get("result")})


@app.route("/admin/relay", methods=["POST"])
def admin_relay():
    r = require_admin()
    if r:
        return r
    s = store.get_settings()
    s["relay_mode"] = request.form.get("relay_mode", "none")
    s["relay_proxy"] = request.form.get("relay_proxy", "").strip()
    s["relay_base_url"] = request.form.get("relay_base_url", "").strip().rstrip("/")
    s["force_ipv4"] = request.form.get("force_ipv4") == "on"
    store.save_settings(s)
    apply_network_settings()
    flash("مسیر اتصال ذخیره و اعمال شد ✓ — دوباره تست اتصال را بگیرید.")
    return redirect(url_for("admin"))


# ---------- ورود تلگرام (روش اول: Telethon) ----------
@app.route("/admin/telegram/send-code", methods=["POST"])
def tg_send_code():
    r = require_admin()
    if r:
        return r
    try:
        api_id = int(request.form.get("api_id", "") or 0)
        api_hash = request.form.get("api_hash", "").strip()
        phone = request.form.get("phone", "").strip()
        if not (api_id and api_hash and phone):
            raise ValueError("همه فیلدها را پر کنید.")
        # Keep API credentials in the persistent settings file, not only in process memory.
        saved = store.get_settings()
        saved["api_id"] = str(api_id)
        saved["api_hash"] = api_hash
        store.save_settings(saved)
        res = tg_client.send_code(api_id, api_hash, phone)
        if not res["ok"]:
            raise ValueError(res["error"])
        session["tg_tmp"] = {"session": res["session"],
                             "phone_code_hash": res["phone_code_hash"],
                             "api_id": api_id, "api_hash": api_hash,
                             "phone": phone}
        flash("کد ورود ارسال شد؛ کد ۵ رقمی را در فیلد کد وارد کنید.")
    except Exception as e:
        flash(f"خطا: {e}")
    return redirect(url_for("admin"))


@app.route("/admin/telegram/signin", methods=["POST"])
def tg_signin():
    r = require_admin()
    if r:
        return r
    tmp = session.get("tg_tmp")
    if not tmp:
        flash("اول شماره را وارد و کد درخواست کنید.")
        return redirect(url_for("admin"))
    res = tg_client.sign_in(tmp["api_id"], tmp["api_hash"], tmp["session"],
                            tmp["phone"], request.form.get("code", "").strip(),
                            tmp["phone_code_hash"],
                            request.form.get("password", "").strip())
    if res["ok"]:
        s = store.get_settings()
        s["tg_session"] = res["session"]
        store.save_settings(s)
        session.pop("tg_tmp", None)
        flash(f"ورود موفق ({res.get('name', '').strip()}) و ذخیره سشن ✓")
    else:
        flash(f"خطا: {res['error']}")
    return redirect(url_for("admin"))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)