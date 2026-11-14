"""موتور Telethon: اتصال با StringSession، ورود دو مرحله‌ای و دریافت پیام‌ها.

مسیر اتصال: به‌صورت پیش‌فرض مستقیم است. اگر سرور میزبان به تلگرام دسترسی نداشته باشد
(مثلاً دیتاسنترهای ایران)، از پنل مدیریت یکی از دو رله اختیاری فعال می‌شود:
  ۱) پروکسی SOCKS5/HTTP  → هر دو موتور telethon و web را پشتیبانی می‌کند
  ۲) آدرس پایه سفارشی (مثل Cloudflare Worker جلوی t.me) → فقط موتور web
"""
import asyncio
import os
import re
from urllib.parse import urlparse

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    SessionPasswordNeededError, FloodWaitError,
    ChannelPrivateError, UsernameNotOccupiedError,
)

# ---------- تنظیم رله (از پنل مدیریت اعمال می‌شود) ----------
_relay = {"mode": "none", "proxy": "", "base_url": ""}


def configure_relay(mode: str = "none", proxy: str = "", base_url: str = ""):
    """تنظیم مسیر اتصال؛ mode: none | proxy | base_url."""
    _relay["mode"] = (mode or "none").strip() or "none"
    _relay["proxy"] = (proxy or "").strip()
    _relay["base_url"] = (base_url or "").strip().rstrip("/")


def relay_settings() -> dict:
    return dict(_relay)


def proxy_from_config():
    """پروکسی فعال را به تنظیمات Telethon تبدیل می‌کند؛ None یعنی اتصال مستقیم."""
    if _relay["mode"] != "proxy":
        return None
    raw = _relay["proxy"]
    if not raw:
        return None
    if "://" not in raw:
        raw = "socks5://" + raw
    u = urlparse(raw)
    if not u.hostname:
        return None
    scheme = u.scheme or "socks5"
    p = {"proxy_type": scheme, "host": u.hostname,
         "port": u.port or (1080 if scheme.startswith("socks") else 8080),
         "rdns": True}
    if u.username:
        p["username"] = u.username
    if u.password:
        p["password"] = u.password
    return p


def proxies_for_requests():
    """همان پروکسی در قالب dict پارامتر proxies برای کتابخانه requests."""
    p = proxy_from_config()
    if not p:
        return None
    scheme = "socks5h" if str(p["proxy_type"]).startswith("socks") else str(p["proxy_type"])
    auth = ""
    if p.get("username"):
        auth = p["username"] + ":" + p.get("password", "") + "@"
    base = f"{scheme}://{auth}{p['host']}:{p['port']}"
    return {"http": base, "https": base}


def proxy_conn_target():
    """میزبان و پورت پروکسی برای تست اتصال TCP؛ None یعنی بدون پروکسی."""
    p = proxy_from_config()
    if not p:
        return None
    return (p["host"], p["port"])


def telegram_web_base() -> str:
    """آدرس پایه پیش‌نمایش وب؛ در حالت base_url همان رله سفارشی است."""
    if _relay["mode"] == "base_url" and _relay["base_url"]:
        return _relay["base_url"]
    return "https://t.me"


def _run(job):
    """اجرای کوروتین در یک event loop تازه (ایمن برای thread پس‌زمینه)."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(job(loop))
    finally:
        loop.close()


def _make(session_string, api_id, api_hash, loop):
    """ساخت کلاینت؛ اگر پروکسی فعال باشد از آن استفاده می‌شود، وگرنه اتصال مستقیم."""
    return TelegramClient(StringSession(session_string), api_id, api_hash,
                          loop=loop, proxy=proxy_from_config())


async def _close(client):
    try:
        await client.disconnect()
    except Exception:
        pass


def send_code(api_id: int, api_hash: str, phone: str) -> dict:
    """ارسال کد ورود تلگرام؛ خروجی: session موقت + phone_code_hash."""
    async def job(loop):
        client = _make("", api_id, api_hash, loop)
        try:
            await client.connect()
            sent = await client.send_code_request(phone)
            return {"ok": True, "session": client.session.save(),
                    "phone_code_hash": sent.phone_code_hash}
        except Exception as e:
            return {"ok": False, "error": str(e)}
        finally:
            await _close(client)
    return _run(job)


def sign_in(api_id: int, api_hash: str, session_string: str,
            phone: str, code: str, phone_code_hash: str,
            password: str = "") -> dict:
    """تکمیل ورود با کد؛ در صورت 2FA از password استفاده می‌کند.
    خروجی: session دائمی (StringSession) برای ذخیره در تنظیمات."""
    async def job(loop):
        client = _make(session_string, api_id, api_hash, loop)
        try:
            await client.connect()
            try:
                me = await client.sign_in(phone=phone, code=code,
                                          phone_code_hash=phone_code_hash)
            except SessionPasswordNeededError:
                if not password:
                    return {"ok": False,
                            "error": "این حساب رمز دوم (2FA) دارد؛ فیلد رمز را پر کنید."}
                me = await client.sign_in(password=password)
            name = (getattr(me, "first_name", "") or "") + " " + \
                   (getattr(me, "last_name", "") or "")
            return {"ok": True, "session": client.session.save(), "name": name.strip()}
        except Exception as e:
            return {"ok": False, "error": str(e)}
        finally:
            await _close(client)
    return _run(job)


def fetch_messages(api_id: int, api_hash: str, session_string: str,
                   channel: str, limit: int, min_id: int, media_root: str = "") -> dict:
    """دریافت پیام‌های کانال (جدید به قدیم) بزرگ‌تر از min_id."""
    async def job(loop):
        client = _make(session_string, api_id, api_hash, loop)
        try:
            await client.connect()
            entity = await client.get_entity(channel)
            out = []
            kwargs = {"limit": int(limit) if int(limit) > 0 else None}
            if min_id:
                kwargs["min_id"] = int(min_id)
            async for m in client.iter_messages(entity, **kwargs):
                media = ("photo" if m.photo else "video" if m.video
                         else "audio" if (m.voice or m.audio)
                         else "document" if m.document else None)
                item = {
                    "id": m.id,
                    "date": m.date.isoformat() if m.date else None,
                    "text": m.raw_text or "",
                    "views": m.views,
                    "forwards": m.forwards,
                    "has_media": media,
                    # آلبوم‌های تلگرام (چند عکس در یک پست) شناسه گروه مشترک دارند
                    "media_group": str(m.grouped_id) if getattr(m, "grouped_id", None) else None,
                    "link": f"https://t.me/{channel}/{m.id}",
                    "media_name": None,
                    "media_path": None,
                    "media_mime": getattr(m.document, "mime_type", None) if m.document else None,
                }
                # دانلود رسانه‌ها؛ خطای یک فایل نباید کل دریافت را متوقف کند.
                # MAX_MEDIA_MB سقف حجم است (پیش‌فرض 40) — فایل بزرگ‌تر دانلود نمی‌شود
                # تا رم/دیسک سرور محافظت شود و فقط متادیتای آن ذخیره می‌شود.
                if media_root and m.media:
                    max_mb = float(os.environ.get("MAX_MEDIA_MB") or 40)
                    file_size = getattr(getattr(m, "file", None), "size", None)
                    if file_size and file_size > max_mb * 1024 * 1024:
                        item["media_skipped"] = f"بزرگ‌تر از سقف {max_mb:g}MB"
                    else:
                        try:
                            safe_channel = re.sub(r"[^A-Za-z0-9_.-]", "_", str(channel))
                            folder = os.path.join(media_root, safe_channel)
                            os.makedirs(folder, exist_ok=True)
                            original = getattr(getattr(m, "file", None), "name", None)
                            if not original:
                                ext = ".jpg" if media == "photo" else ".mp4" if media == "video" else ".bin"
                                original = f"{m.id}{ext}"
                            original = os.path.basename(original).replace("\\", "_")
                            # ID پیام در نام ذخیره‌سازی، جلوی تداخل نام فایل‌های هم‌نام را می‌گیرد.
                            stem, ext = os.path.splitext(original)
                            local_name = f"{m.id}_{stem[:100]}{ext[:16]}"
                            target = os.path.join(folder, local_name)
                            if not os.path.exists(target):
                                saved_path = await client.download_media(m, file=target)
                                if saved_path and os.path.isfile(saved_path):
                                    item["media_name"] = original
                                    item["media_path"] = os.path.relpath(saved_path, media_root).replace(os.sep, "/")
                        except Exception as media_error:
                            item["media_error"] = str(media_error)[:300]
                out.append(item)
            return {"ok": True, "messages": out,
                    "title": getattr(entity, "title", channel)}
        except FloodWaitError as e:
            return {"ok": False, "error": f"محدودیت تلگرام: {e.seconds} ثانیه صبر کنید."}
        except ChannelPrivateError:
            return {"ok": False, "error": "کانال خصوصی است و قابل خواندن نیست."}
        except UsernameNotOccupiedError:
            return {"ok": False, "error": "این نام کانال وجود ندارد."}
        except Exception as e:
            return {"ok": False, "error": str(e)}
        finally:
            await _close(client)
    return _run(job)
