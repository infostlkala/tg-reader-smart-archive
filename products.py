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
    الگوی واقعی کانال: ویدئو/آلبوم عکسِ معرفی (با متن محصول) و بلافاصله بعدش
    پست فایل zip/stl. خروجی: [{file: msg, companions: [msg, ...]}] —
    companions شامل هر رسانه غیرفایل (عکس/ویدئو) مجاور فایل است."""
    msgs = [m for m in msgs if m.get("has_media")]
    groups = []
    buffer = []  # رسانه‌های غیرفایل اخیر (جدید→قدیم)
    from datetime import datetime
    def dt(m):
        try:
            return datetime.fromisoformat((m.get("date") or "").replace("Z", "+00:00"))
        except ValueError:
            return None
    for m in msgs:
        if _file_post(m):
            d = dt(m)
            kept = []
            for p in buffer:
                dp = dt(p)
                if d and dp and (d - dp).total_seconds() <= max_gap_hours * 3600:
                    kept.append(p)
            groups.append({"file": m, "photos": kept})  # photos = همه رسانه‌های همراه
            buffer = []
        else:
            buffer.append(m)
    return groups


def context_texts(msgs: list, file_id: str, window: int = 2) -> dict:
    """متن پست‌های متنیِ نزدیک (هر نوع، شامل متن‌دار) در پنجره id —
    چون متن محصول معمولاً روی پست ویدئویی/عکسیِ کنار فایل است."""
    out = {}
    ordered = sorted(msgs, key=lambda m: int(m["id"]), reverse=True)
    idx = next((i for i, m in enumerate(ordered) if str(m["id"]) == str(file_id)), None)
    if idx is None:
        return out
    for m in ordered[max(0, idx - window): idx + window + 1]:
        t = (m.get("text") or "").strip()
        if t and str(m["id"]) != str(file_id):
            out[str(m["id"])] = t[:300]
    return out


AI_PROMPT = (
    "You group Telegram posts of a 3D-printing channel into products. "
    "The channel's pattern: a video/photo post introducing the product "
    "(with the product name in its caption) followed immediately by the "
    "file post (zip/stl) of that same product. Given the file post's "
    "filename and the nearby post captions below, decide if these posts "
    "belong to ONE product. Return ONLY valid JSON with keys: "
    '"is_same_product" (bool), "folder_name" (short English slug like '
    '"gyro-cup-stroller" — no spaces, lowercase, hyphens), '
    '"confidence" (0-1). Posts JSON:\n{payload}\n'
)


def ai_confirm(group: dict, base_url: str, api_key: str, model: str,
               all_msgs: list | None = None) -> dict:
    """پرسش از مدل سازگار با OpenAI: این مجموعه یک محصول است؟ نام پوشه؟
    all_msgs برای گرفتن متن پست‌های متنی مجاور فایل (ویدئوی معرفی و...)."""
    payload = []
    for p in group["photos"]:
        payload.append({"type": p.get("has_media") or "media", "id": p["id"],
                        "text": (p.get("text") or "")[:300]})
    f = group["file"]
    payload.append({"type": "file", "id": f["id"],
                    "filename": f.get("media_name"),
                    "text": (f.get("text") or "")[:300]})
    if all_msgs:
        for mid, txt in context_texts(all_msgs, f["id"]).items():
            payload.append({"type": "caption-nearby", "id": mid, "text": txt})
    prompt = AI_PROMPT.format(payload=json.dumps(payload, ensure_ascii=False))
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
                continue
            last_err = e
        except (requests.Timeout, requests.ConnectionError,
                json.JSONDecodeError, KeyError, ValueError) as e:
            last_err = e
    raise last_err
