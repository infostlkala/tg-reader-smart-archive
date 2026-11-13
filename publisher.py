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
    """POST متادیتا به receiver.php روی cPanel."""
    import requests
    r = requests.post(
        cfg["endpoint_url"],
        json=payload,
        headers={"X-Auth-Secret": cfg["secret"], "Content-Type": "application/json"},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json() if "json" in (r.headers.get("Content-Type") or "") else {}
    if not data.get("ok"):
        raise RuntimeError("endpoint پاسخ ناموفق: " + str(data)[:300])
    return data


def publish_message(msg: dict, cfg: dict | None = None, media_root: str = "") -> dict:
    """یک پیام را منتشر می‌کند: FTP رسانه (در صورت وجود) + POST متادیتا.
    پیام را درجا ویرایش می‌کند: media_path به مسیر روی هاست و media_url به آدرس عمومی تبدیل می‌شود.
    خروجی: {"ok": bool, "ftp": ..., "post": ...} — فقط برای لاگ."""
    cfg = cfg or publish_config()
    result = {"ok": False, "ftp": "-", "post": "-"}

    # ۱) رسانه: دانلود قبلاً در media_root انجام شده؛ حالا FTP و پاک‌سازی تمپ
    local_media = None
    if msg.get("media_path") and media_root:
        local_media = os.path.join(media_root, msg["media_path"].replace("/", os.sep))

    if local_media and os.path.isfile(local_media):
        if cfg["ftp_host"] and cfg["ftp_user"]:
            try:
                channel = msg.get("channel") or "media"
                remote_name = os.path.basename(msg["media_path"])
                ftp_upload(local_media, cfg["ftp_remote_dir"] + "/" + channel,
                           remote_name, cfg)
                rel = channel + "/" + remote_name
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
            "text", "views", "forwards", "has_media", "media_name", "media_path",
            "media_url", "media_mime", "link", "ai_status")}
        payload["id"] = str(payload.get("id"))
        post_metadata(payload, cfg)
        result["post"] = "ok"
    else:
        result["post"] = "skipped (endpoint تنظیم نشده)"

    result["ok"] = result["post"] == "ok"
    return result
