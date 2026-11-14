"""گروه‌بندی هوشمند پست‌های یک محصول (الگوی کانال‌های مدل سه‌بعدی):
   پست/آلبومِ عکس‌های محصول → پستِ فایل دانلودی (zip/stl/3mf) زیرِ آن.

الگوریتم:
 ۱) کاندید‌یابی قاعده‌مند: پست‌های متوالی جدید→قدیم؛ هر پستِ «فایل‌دار» همه
    پست‌های رسانه‌ای «بدون فایل» پیش از خودش (تا شکاف زمانی max_gap) را
    به‌عنوان عکس‌های همان محصول می‌گیرد.
 ۲) تأیید AI: متن پست‌ها + نام فایل به مدل داده می‌شود؛ مدل می‌گوید این
    مجموعه یک محصول واحد است یا نه و نام انگلیسی تمیز برای پوشه می‌سازد.
 ۳) خروجی: گروه‌های تأییدشده با شناسه پست فایل + شناسه پست‌های عکس.
"""
import json
import re

import requests

# پست‌های فایل‌دار: پسوندهای مدل سه‌بعدی/آرشیو
FILE_EXTS = {".zip", ".rar", ".7z", ".stl", ".3mf", ".obj", ".step", ".stp",
             ".gcode", ".amf", ".ply"}


def _file_post(m: dict) -> bool:
    name = (m.get("media_name") or "").lower()
    return bool(name) and any(name.endswith(e) for e in FILE_EXTS)


def _photo_post(m: dict) -> bool:
    return m.get("has_media") == "photo" and not _file_post(m)


def _slug(text: str, fallback: str) -> str:
    """نام پوشه انگلیسی امن: حروف/عدد/خط تیره، حداکثر ۶۰ کاراکتر."""
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    text = re.sub(r"-{2,}", "-", text)[:60].strip("-")
    return text or fallback


def candidate_groups(msgs: list, max_gap_hours: float = 48) -> list:
    """msgs: پیام‌های رسانه‌دار یک کانال، مرتب جدید→قدیم (خروجی store).
    خروجی: [{file: msg, photos: [msg, ...]}] بر اساس الگوی کانال."""
    msgs = [m for m in msgs if m.get("has_media")]
    groups = []
    photos_buffer = []  # پست‌های عکس که هنوز فایلشان نیامده (جدید→قدیم)
    from datetime import datetime, timezone
    def dt(m):
        try:
            return datetime.fromisoformat((m.get("date") or "").replace("Z", "+00:00"))
        except ValueError:
            return None
    for m in msgs:
        if _file_post(m):
            d = dt(m)
            kept = []
            for p in photos_buffer:
                dp = dt(p)
                if d and dp and (d - dp).total_seconds() <= max_gap_hours * 3600:
                    kept.append(p)
            if kept:
                groups.append({"file": m, "photos": list(reversed(kept))})
            photos_buffer = []
        elif _photo_post(m):
            photos_buffer.append(m)
        # سایر رسانه‌ها (ویدئو و...) فعلاً نادیده
    return groups


AI_PROMPT = (
    "You group Telegram posts of a 3D-printing channel into products. "
    "A product = several photo posts + one file post (zip/stl) that belongs "
    "to the same 3D model. Given the posts below, decide if they form ONE "
    "product. Return ONLY valid JSON with keys: "
    '"is_same_product" (bool), "folder_name" (short English slug like '
    '"gyro-cup-stroller" — no spaces, lowercase, hyphens), '
    '"confidence" (0-1). Posts JSON:\n{payload}\n'
)


def ai_confirm(group: dict, base_url: str, api_key: str, model: str) -> dict:
    """پرسش از مدل سازگار با OpenAI: این مجموعه یک محصول است؟ نام پوشه؟"""
    payload = []
    for p in group["photos"]:
        payload.append({"type": "photo", "id": p["id"],
                        "text": (p.get("text") or "")[:300]})
    f = group["file"]
    payload.append({"type": "file", "id": f["id"],
                    "filename": f.get("media_name"),
                    "text": (f.get("text") or "")[:300]})
    prompt = AI_PROMPT.format(payload=json.dumps(payload, ensure_ascii=False))
    # مدل اصلی اول؛ اگر پاسخ نداد (timeout/خطا) مدل‌های جایگزین سرویس امتحان می‌شوند
    models = [model, "gpt-5.4-mini", "claude-haiku-5-5"]
    seen = set()
    last_err = None
    for m in models:
        if m in seen or not m:
            continue
        seen.add(m)
        try:
            r = requests.post(base_url.rstrip("/") + "/chat/completions",
                              headers={"Authorization": "Bearer " + api_key,
                                       "Content-Type": "application/json"},
                              json={"model": m, "temperature": 0.1,
                                    "messages": [{"role": "system",
                                                  "content": "Return only valid JSON."},
                                                 {"role": "user", "content": prompt}]},
                              timeout=60)
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"].strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            data = json.loads(content)
            fallback = "product-" + str(group["file"]["id"])
            data["folder_name"] = _slug(str(data.get("folder_name") or ""), fallback)
            return data
        except requests.HTTPError as e:
            if "404" in str(e) or "400" in str(e):
                continue  # مدل در این سرویس نیست؛ برو سراغ بعدی
            last_err = e
        except (requests.Timeout, requests.ConnectionError,
                json.JSONDecodeError, KeyError, ValueError) as e:
            last_err = e
    raise last_err
