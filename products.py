"""گروه‌بندی محصولات کانال — الگوریتم v5 (دو مرحله‌ای، مبتنی بر تحلیل داده).

الگوی واقعی کانال (۱۴۶ پیام تحلیل شد):
- هر محصول در یک «بسته ارسال» پست می‌شود: کپشن معرفی (ویدئو/عکس) + عکس‌های
  آلبوم + فایل zip/stl — همه هم‌ثانیه یا فاصله چند ثانیه (p90 gap = 1 دقیقه).
- گاهی چند محصول پشت‌سرهم در یک بسته ارسال می‌شوند (gap=0 بین‌شان).
- فایل‌های دورهای اولیه media_name ندارند؛ mime=application/zip.

الگوریتم:
 مرحله ۱ (قطعی، محاسباتی): خوشه زمانی با gap > GAP_SECONDS بین پست‌های
   متوالی = مرز بسته.
 مرحله ۲ (AI، فقط در صورت نیاز): اگر بسته بیش از یک کپشن یا بیش از یک فایل
   دارد، AI کپشن‌ها را به فایل‌ها می‌چسباند (matching) و نام پوشه می‌سازد.
   بسته‌های تک‌محصولی مستقیم نام‌گذاری می‌شوند.
"""
import json
import re
from datetime import datetime

FILE_EXTS = (".zip", ".rar", ".7z", ".stl", ".3mf", ".obj", ".step", ".stp",
             ".gcode", ".amf", ".ply")
ZIP_MIMES = ("application/zip", "application/x-rar-compressed",
             "application/x-7z-compressed")


def is_file_post(m: dict) -> bool:
    name = (m.get("media_name") or "").lower()
    if any(name.endswith(e) for e in FILE_EXTS):
        return True
    return (m.get("media_mime") or "").lower() in ZIP_MIMES


def has_caption(m: dict) -> bool:
    return len((m.get("text") or "").strip()) >= 10


def _dt(m: dict):
    try:
        return datetime.fromisoformat((m.get("date") or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def _slug(text: str, fallback: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    text = re.sub(r"-{2,}", "-", text)[:60].strip("-")
    return text or fallback


def slug_from_caption(caption: str, fallback: str) -> str:
    first_line = caption.strip().splitlines()[0] if caption.strip() else ""
    cleaned = re.sub(r"[^\w\s-]", "", first_line, flags=re.UNICODE)
    return _slug(cleaned, fallback)


def time_batches(msgs: list, gap_seconds: float = 120) -> list:
    """مرحله ۱: خوشه زمانی. msgs مرتب جدید→قدیم.
    خروجی: بسته‌ها به ترتیب جدید→قدیم، هر بسته خودش مرتب جدید→قدیم."""
    batches = []
    cur = []
    prev = None
    for m in msgs:
        d = _dt(m)
        if prev is not None and d and prev:
            if (prev - d).total_seconds() > gap_seconds:
                batches.append(cur)
                cur = []
        cur.append(m)
        prev = d
    if cur:
        batches.append(cur)
    return batches


MATCH_PROMPT = (
    "A Telegram 3D-printing channel posted this batch: several caption posts "
    "(product intros, may include non-product announcements like giveaways) "
    "and several file posts (zip/stl). Match each file to its product caption "
    "by name similarity. Announcements with no matching file become their own "
    "group. Return ONLY valid JSON: {\"groups\": [{\"caption_id\": int|null, "
    "\"caption_text\": str, \"file_ids\": [int], \"folder_name\": \"english-slug\", "
    "\"title\": \"عنوان فارسی\", \"description\": \"توضیح فارسی کوتاه\", "
    "\"tags\": [\"تگ\"], \"category\": \"مدل سه‌بعدی|تصویر|ویدئو|سند|آرشیو|سایر\"}]}. "
    "Every file id and every caption id must appear exactly once. Batch JSON:\n{payload}\n"
)


def _call_ai(prompt: str, base_url: str, api_key: str, model: str,
             fallback_models: list) -> dict:
    import requests
    last_err = None
    for mdl in [model] + [m for m in fallback_models if m != model]:
        if not mdl:
            continue
        try:
            r = requests.post(base_url.rstrip("/") + "/chat/completions",
                              headers={"Authorization": "Bearer " + api_key,
                                       "Content-Type": "application/json"},
                              json={"model": mdl, "temperature": 0.1,
                                    "messages": [{"role": "system",
                                                  "content": "Return only valid JSON."},
                                                 {"role": "user", "content": prompt}]},
                              timeout=90)
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"].strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            return json.loads(content)
        except requests.HTTPError as e:
            if "404" in str(e) or "400" in str(e):
                continue
            last_err = e
        except (requests.Timeout, requests.ConnectionError, ValueError) as e:
            last_err = e
    raise last_err


def split_batch_with_ai(batch: list, base_url: str, api_key: str, model: str,
                        fallback_models: list | None = None) -> list:
    """مرحله ۲: تفکیک بسته چندمحصولی با AI. خروجی: لیست گروه‌های محصول:
    [{caption: msg|None, members: [msg...], folder_name, title, description,
      tags, category, ai_used: True}]"""
    payload = []
    for m in batch:
        entry = {"id": int(m["id"]), "type": m.get("has_media"),
                 "is_file": is_file_post(m),
                 "filename": m.get("media_name")}
        if has_caption(m):
            entry["caption"] = (m.get("text") or "")[:500]
        payload.append(entry)
    prompt = MATCH_PROMPT.replace("{payload}", json.dumps(payload, ensure_ascii=False))
    try:
        data = _call_ai(prompt, base_url, api_key, model,
                        fallback_models or ["gpt-5.4-mini", "claude-haiku-5-5"])
    except Exception as ai_err:
        # AI در دسترس نیست: هر فایل یک گروه با نام فایل + رسانه‌های بین فایل‌ها
        print(f"[ai-split] AI unavailable ({str(ai_err)[:60]}) → filename fallback",
              flush=True)
        return _filename_fallback_groups(batch)
    byid = {int(m["id"]): m for m in batch}
    used = set()
    groups = []
    for g in data.get("groups", []):
        members = []
        cap = None
        cap_id = g.get("caption_id")
        if cap_id and int(cap_id) in byid and int(cap_id) not in used:
            cap = byid[int(cap_id)]
            members.append(cap)
            used.add(int(cap_id))
        for fid in g.get("file_ids") or []:
            fid = int(fid)
            if fid in byid and fid not in used:
                members.append(byid[fid])
                used.add(fid)
        if not members:
            continue
        folder = _slug(str(g.get("folder_name") or ""),
                       f"product-{members[0]['id']}")
        groups.append({"caption": cap, "members": members,
                       "folder_name": folder, "title": g.get("title"),
                       "description": g.get("description"),
                       "tags": g.get("tags") or [],
                       "category": g.get("category"), "ai_used": True})
    # رسانه‌های بی‌گروه (AI جا انداخته) → گروه جدا با slug کپشن/نام فایل
    for m in batch:
        if int(m["id"]) not in used:
            slug = slug_from_caption((m.get("text") or ""),
                                     f"product-{m['id']}")
            groups.append({"caption": m if has_caption(m) else None,
                           "members": [m], "folder_name": slug,
                           "ai_used": False})
    return groups


def _filename_fallback_groups(batch: list) -> list:
    """بدون AI: فایل‌ها مرز هستند — رسانه‌های بین دو فایل به فایل قدیمی‌تر
    می‌چسبند (الگوی کانال: کپشن و رسانه‌ها قبل/هم‌زمان فایل خودشان)."""
    groups = []
    cur_members = []
    cur_slug = None
    for m in batch:  # جدید → قدیم
        if is_file_post(m):
            # فایل جدید: گروه قبلی (رسانه‌های جلویش) را ببند
            stem = (m.get("media_name") or m.get("media_mime") or "").rsplit(".", 1)[0]
            slug = _slug(stem, f"product-{m['id']}")
            if cur_members:
                groups.append({"caption": None, "members": cur_members,
                               "folder_name": cur_slug or slug, "ai_used": False})
            cur_members = [m]
            cur_slug = slug
        else:
            cur_members.append(m)
    if cur_members:
        groups.append({"caption": None, "members": cur_members,
                       "folder_name": cur_slug or "media", "ai_used": False})
    return groups


def name_batch(batch: list, base_url: str, api_key: str, model: str,
               fallback_models: list | None = None) -> list:
    """نام‌گذاری بسته: تک‌محصولی مستقیم؛ چندمحصولی با AI split.
    خروجی مثل split_batch_with_ai ولی ai_used ممکن است False باشد."""
    caps = [m for m in batch if has_caption(m)]
    files = [m for m in batch if is_file_post(m)]
    if len(files) <= 1 and len(caps) <= 1:
        # تک‌محصولی: نام از کپشن
        cap = caps[0] if caps else None
        cap_text = (cap.get("text") or "") if cap else ""
        first_file = files[0] if files else None
        fallback = slug_from_caption(cap_text,
                                     f"product-{batch[0]['id']}")
        groups = [{"caption": cap, "members": list(batch),
                   "folder_name": fallback, "ai_used": False}]
        if cap_text and (base_url and api_key and model):
            try:
                meta = _call_ai(
                    "Caption of a 3D-printing product post (Russian/Persian). "
                    "Return ONLY valid JSON: {\"folder_name\": \"english-slug\", "
                    "\"title\": \"عنوان فارسی\", \"description\": \"توضیح فارسی\", "
                    "\"tags\": [\"تگ\"], \"category\": \"...\"}. Caption:\n"
                    + json.dumps({"caption": cap_text[:500],
                                  "filename": first_file.get("media_name") if first_file else None},
                                 ensure_ascii=False),
                    base_url, api_key, model, fallback_models or
                    ["gpt-5.4-mini", "claude-haiku-5-5"])
                groups[0].update({
                    "folder_name": _slug(str(meta.get("folder_name") or ""), fallback),
                    "title": meta.get("title"),
                    "description": meta.get("description"),
                    "tags": meta.get("tags") or [],
                    "category": meta.get("category"),
                    "ai_used": True})
            except Exception:
                pass
        return groups
    # چندمحصولی → AI matching
    return split_batch_with_ai(batch, base_url, api_key, model, fallback_models)
