"""انتشار پیام‌ها به هاست cPanel: آپلود FTP رسانه + POST متادیتا به endpoint PHP.

متغیرهای محیطی:
  PUBLISH_ENDPOINT_URL  مثل https://example.com/tg-receiver.php
  PUBLISH_SECRET        رمز مشترک؛ در receiver.php هم باید همان باشد
  FTP_HOST / FTP_PORT   هاست و پورت FTP (پیش‌فرض 21)
  FTP_USER / FTP_PASS   اطلاعات ورود FTP
  FTP_SECURE            1 = FTPS (ضریب امنیت؛ اگر هاست پشتیبانی کند)
  FTP_REMOTE_DIR        پوشه مقصد روی هاست، مثل public_html/wp-content/uploads/tg
  MEDIA_PUBLIC_BASE_URL آدرس عمومی همان پوشه، مثل https://example.com/wp-content/uploads/tg
  MAX_MEDIA_MB          سقف حجم فایل برای دانلود/آپلود (پیش‌فرض 40)
اگر PUBLISH_ENDPOINT_URL و PUBLISH_SECRET ست نباشند، انتشار غیرفعال است و
رفتار مثل قبل (ذخیره محلی) باقی می‌ماند.
"""
import os
import ftplib
import json
import shutil
import subprocess
import tempfile


def publish_config() -> dict:
    return {
        "endpoint_url": (os.environ.get("PUBLISH_ENDPOINT_URL") or "").strip(),
        "secret": (os.environ.get("PUBLISH_SECRET") or "").strip(),
        "ftp_host": (os.environ.get("FTP_HOST") or "").strip(),
        "ftp_port": int(os.environ.get("FTP_PORT") or 21),
        "ftp_user": (os.environ.get("FTP_USER") or "").strip(),
        "ftp_pass": (os.environ.get("FTP_PASS") or "").strip(),
        "ftp_secure": (os.environ.get("FTP_SECURE") or "").strip() == "1",
        "ftp_remote_dir": (os.environ.get("FTP_REMOTE_DIR") or "public_html/tg-media").strip().rstrip("/"),
        "media_base_url": (os.environ.get("MEDIA_PUBLIC_BASE_URL") or "").strip().rstrip("/"),
        "max_media_mb": float(os.environ.get("MAX_MEDIA_MB") or 40),
    }


def publish_enabled(cfg: dict | None = None) -> bool:
    cfg = cfg or publish_config()
    return bool(cfg["endpoint_url"] and cfg["secret"])


def _ftp_connect(cfg: dict):
    cls = ftplib.FTP_TLS if cfg["ftp_secure"] else ftplib.FTP
    ftp = cls()
    ftp.connect(cfg["ftp_host"], cfg["ftp_port"], timeout=60)
    ftp.login(cfg["ftp_user"], cfg["ftp_pass"])
    if cfg["ftp_secure"]:
        ftp.prot_p()  # کانال داده هم رمزشود
    return ftp


def _ftp_ensure_dir(ftp, remote_dir: str):
    """ساخت زنجیره پوشه‌ها روی هاست (مثل mkdir -p)."""
    parts = [p for p in remote_dir.split("/") if p]
    cur = ""
    for part in parts:
        cur += "/" + part
        try:
            ftp.mkd(cur)
        except ftplib.error_perm as e:
            if "550" not in str(e):  # 550 = از قبل وجود دارد
                raise


def ftp_upload(local_path: str, remote_dir: str, remote_name: str, cfg: dict) -> str:
    """آپلود یک فایل؛ اگر از قبل همان نام/حجم باشد رد می‌شود. خروجی: نام فایل."""
    ftp = _ftp_connect(cfg)
    try:
        _ftp_ensure_dir(ftp, remote_dir)
        ftp.cwd(remote_dir)
        try:
            existing = ftp.size(remote_name)
        except ftplib.error_perm:
            existing = None
        if existing is not None and existing == os.path.getsize(local_path):
            return remote_name
        with open(local_path, "rb") as f:
            ftp.storbinary("STOR " + remote_name, f, blocksize=1024 * 256)
        return remote_name
    finally:
        try:
            ftp.quit()
        except Exception:
            try:
                ftp.close()
            except Exception:
                pass


def post_metadata(payload: dict, cfg: dict) -> dict:
    """POST متادیتا به receiver.php روی cPanel.

    فایروال برخی هاست‌های ایرانی ClientHello کتابخانه ssl پایتون (OpenSSL)
    را تشخیص می‌دهد و Connection را reset می‌کند؛ ولی curl (Schannel/GnuTLS)
    رد می‌شود. پس اول با curl subprocess می‌فرستیم و در نبود curl به requests
    برمی‌گردیم."""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    curl = shutil.which("curl")
    if curl:
        fd, tmp_path = tempfile.mkstemp(suffix=".json")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(body)
            cmd = [curl, "-sk", "-m", "30", "-X", "POST", cfg["endpoint_url"],
                   "-H", "X-Auth-Secret: " + cfg["secret"],
                   "-H", "Content-Type: application/json; charset=utf-8",
                   "--data-binary", "@" + tmp_path,
                   "-w", "\n%{http_code}"]
            pr = subprocess.run(cmd, capture_output=True, timeout=40)
            out = pr.stdout.decode("utf-8", "replace")
            if not out.strip():
                raise RuntimeError("curl پاسخ خالی داد" +
                                   (": " + pr.stderr.decode("utf-8", "replace")[:200]
                                    if pr.stderr else ""))
            body_txt, _, http_code = out.strip().rpartition("\n")
            if http_code.strip() not in ("200", "201", "204"):
                raise RuntimeError(f"endpoint HTTP {http_code.strip()}: {body_txt[:200]}")
            try:
                data = json.loads(body_txt)
            except ValueError:
                data = {}
            if not data.get("ok"):
                raise RuntimeError("endpoint پاسخ ناموفق: " + body_txt[:300])
            return data
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
    # لایه ۲: pycurl (انگشت‌نگاری libcurl — فایروال‌های ضد پایتون را رد می‌کند)
    try:
        return _post_with_pycurl(cfg, body)
    except ImportError:
        pass  # نصب نیست؛ به requests می‌رویم
    # لایه ۳ (fallback): requests — روی هاست‌هایی که فیلتر TLS ندارند
    import requests
    r = requests.post(
        cfg["endpoint_url"],
        data=body,
        headers={"X-Auth-Secret": cfg["secret"],
                 "Content-Type": "application/json; charset=utf-8"},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json() if "json" in (r.headers.get("Content-Type") or "") else {}
    if not data.get("ok"):
        raise RuntimeError("endpoint پاسخ ناموفق: " + str(data)[:300])
    return data


def _post_with_pycurl(cfg: dict, body: bytes) -> dict:
    import pycurl
    import io
    buf = io.BytesIO()
    c = pycurl.Curl()
    c.setopt(c.URL, cfg["endpoint_url"])
    c.setopt(c.POST, 1)
    c.setopt(c.POSTFIELDS, body)
    c.setopt(c.HTTPHEADER, ["X-Auth-Secret: " + cfg["secret"],
                            "Content-Type: application/json; charset=utf-8"])
    c.setopt(c.WRITEDATA, buf)
    c.setopt(c.TIMEOUT, 30)
    c.setopt(c.HTTP_VERSION, pycurl.CURL_HTTP_VERSION_1_1)
    c.setopt(c.SSL_VERIFYPEER, 0)
    c.setopt(c.SSL_VERIFYHOST, 0)
    c.perform()
    status = c.getinfo(c.RESPONSE_CODE)
    c.close()
    text = buf.getvalue().decode("utf-8", "replace")
    if status not in (200, 201, 204):
        raise RuntimeError(f"endpoint HTTP {status}: {text[:200]}")
    try:
        data = json.loads(text)
    except ValueError:
        data = {}
    if not data.get("ok"):
        raise RuntimeError("endpoint پاسخ ناموفق: " + text[:300])
    return data


def media_subdir(msg: dict) -> str:
    """پوشه مقصد رسانه روی هاست؛ ساختار مرتب per-post:
    - آلبوم (چند رسانه در یک پست): <channel>/album-<grouped_id>/
    - پیام تکی: <channel>/post-<id>/
    کانال خودش در FTP_REMOTE_DIR پایه است."""
    channel = str(msg.get("channel") or "media")
    group = msg.get("media_group")
    if group:
        return f"{channel}/album-{group}"
    return f"{channel}/post-{msg.get('id', 'x')}"


def publish_message(msg: dict, cfg: dict | None = None, media_root: str = "",
                    channel: str = "") -> dict:
    """یک پیام را منتشر می‌کند: FTP رسانه (در صورت وجود) + POST متادیتا.
    پیام را درجا ویرایش می‌کند: media_path به مسیر روی هاست و media_url به آدرس عمومی تبدیل می‌شود.
    channel: نام کانال (پیام‌های store این فیلد را ندارند).
    خروجی: {"ok": bool, "ftp": ..., "post": ...} — فقط برای لاگ."""
    cfg = cfg or publish_config()
    if channel:
        msg["channel"] = channel
    result = {"ok": False, "ftp": "-", "post": "-"}

    # ۱) رسانه: دانلود قبلاً در media_root انجام شده؛ حالا FTP و پاک‌سازی تمپ
    local_media = None
    if msg.get("media_path") and media_root:
        local_media = os.path.join(media_root, msg["media_path"].replace("/", os.sep))

    if local_media and os.path.isfile(local_media):
        if cfg["ftp_host"] and cfg["ftp_user"]:
            try:
                remote_name = os.path.basename(msg["media_path"])
                # ساختار مرتب: هر پست/آلبوم در پوشه خودش؛ زیرِ ریشه FTP
                subdir = media_subdir(msg)
                base = cfg["ftp_remote_dir"]
                ftp_upload(local_media, base + "/" + subdir if base else subdir,
                           remote_name, cfg)
                rel = subdir + "/" + remote_name
                msg["media_path"] = rel
                if cfg["media_base_url"]:
                    msg["media_url"] = cfg["media_base_url"] + "/" + rel
                result["ftp"] = "ok"
            except Exception as e:
                result["ftp"] = "error: " + str(e)[:200]
        else:
            result["ftp"] = "skipped (FTP تنظیم نشده)"
        # تمپ را در هر حالت پاک کن — دیسک Railway نباید پر شود
        try:
            os.remove(local_media)
        except OSError:
            pass
    elif msg.get("media_skipped"):
        result["ftp"] = "skipped (" + msg["media_skipped"] + ")"

    # ۲) متادیتا به endpoint
    if publish_enabled(cfg):
        payload = {k: msg.get(k) for k in (
            "id", "channel", "date", "title", "description", "category", "tags",
            "text", "views", "forwards", "has_media", "media_group",
            "media_name", "media_path", "media_url", "media_mime", "link",
            "ai_status")}
        payload["id"] = str(payload.get("id"))
        # پیام‌های ذخیره‌شده در store فیلد channel ندارند؛ از پارامتر پر می‌کنیم
        payload["channel"] = msg.get("channel") or payload.get("channel") or "unknown"
        post_metadata(payload, cfg)
        result["post"] = "ok"
    else:
        result["post"] = "skipped (endpoint تنظیم نشده)"

    result["ok"] = result["post"] == "ok"
    return result
