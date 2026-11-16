"""لایه ذخیره‌سازی: تنظیمات و پیام‌ها در فایل‌های JSON داخل DATA_DIR (دیسک لیارا)."""
import json
import os
import threading
import sqlite3

DEFAULT_SETTINGS = {
    "engine": "telethon",          # telethon (روش اول) | web (بدون نیاز به api)
    "channels": ["telegram"],      # نام کانال‌های عمومی، بدون @
    "history_limit": 100,          # تعداد پیام تاریخچه برای دریافت (0 = کل تاریخچه)
    "web_max_pages": 5,            # فقط موتور web: حداکثر صفحات (هر صفحه ~20 پیام)
    "refresh_interval": 60,        # فواصل بررسی پیام‌های جدید (ثانیه)
    "live_listen": True,           # دریافت زنده پیام جدید (موتور telethon)
    "max_stored": 2000,            # سقف تعداد پیام ذخیره‌شده برای هر کانال
    "display_limit": 50,           # تعداد پیام نمایش‌داده‌شده در صفحه عمومی
    "relay_mode": "none",          # none (مستقیم) | proxy | base_url
    "relay_proxy": "",             # نمونه: socks5://user:pass@1.2.3.4:1080
    "relay_base_url": "",          # نمونه: https://my-worker.example.workers.dev
    "force_ipv4": False,           # اجبار اتصال روی IPv4 (وقتی مسیر IPv6 خراب است)
    "tg_session": "",              # سشن دائمی تلگرام؛ در settings.json ذخیره می‌شود
    "api_id": "",                  # API ID برای استفاده پس از راه‌اندازی مجدد
    "api_hash": "",                # API Hash برای استفاده پس از راه‌اندازی مجدد
    # هوش مصنوعی (از پنل قابل تنظیم؛ اگر خالی باشد از متغیر محیطی استفاده می‌شود)
    "ai_base_url": "",             # مثل https://api.gapgpt.app/v1
    "ai_api_key": "",              # کلید API
    "ai_model": "",                # نام مدل مثل gpt-6-luna
    "ai_vision": True,             # تحلیل بینایی عکس (روشن/خاموش)
}


class Store:
    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self.msg_dir = os.path.join(data_dir, "messages")
        os.makedirs(self.msg_dir, exist_ok=True)
        self.settings_path = os.path.join(data_dir, "settings.json")
        self.db_path = os.path.join(data_dir, "archive.sqlite3")
        self._lock = threading.RLock()
        self._init_db()

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        os.makedirs(self.data_dir, exist_ok=True)
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    channel TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    date TEXT,
                    title TEXT,
                    description TEXT,
                    category TEXT,
                    media_type TEXT,
                    media_name TEXT,
                    media_path TEXT,
                    source_link TEXT,
                    payload_json TEXT NOT NULL,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(channel, message_id)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_category ON messages(category)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_date ON messages(date)")
        # مهاجرت یک‌باره پیام‌های JSON نسخه قبلی به SQLite، بدون حذف فایل‌های قدیمی.
        try:
            for filename in os.listdir(self.msg_dir):
                if not filename.endswith(".json"):
                    continue
                channel = filename[:-5]
                try:
                    with open(os.path.join(self.msg_dir, filename), encoding="utf-8") as f:
                        items = json.load(f)
                    with self._connect() as conn:
                        for m in items if isinstance(items, list) else []:
                            conn.execute("""
                                INSERT OR IGNORE INTO messages
                                (channel, message_id, date, title, description, category,
                                 media_type, media_name, media_path, source_link, payload_json)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """, (channel, str(m.get("id")), m.get("date"), m.get("title"),
                                  m.get("description") or m.get("text"), m.get("category"),
                                  m.get("has_media") if isinstance(m.get("has_media"), str) else None,
                                  m.get("media_name"), m.get("media_path"), m.get("link"),
                                  json.dumps(m, ensure_ascii=False)))
                except Exception:
                    continue
        except OSError:
            pass

    # ---------- تنظیمات ----------
    def get_settings(self) -> dict:
        with self._lock:
            s = dict(DEFAULT_SETTINGS)
            if os.path.exists(self.settings_path):
                try:
                    with open(self.settings_path, encoding="utf-8") as f:
                        saved = json.load(f)
                    if isinstance(saved, dict):
                        s.update(saved)
                except Exception:
                    pass
            return s

    def save_settings(self, s: dict) -> dict:
        with self._lock:
            merged = dict(DEFAULT_SETTINGS)
            merged.update(self.get_settings())
            merged.update({k: s[k] for k in DEFAULT_SETTINGS if k in s})
            with open(self.settings_path, "w", encoding="utf-8") as f:
                json.dump(merged, f, ensure_ascii=False, indent=2)
            return merged

    # ---------- پیام‌ها ----------
    def _msg_path(self, channel: str) -> str:
        safe = "".join(c for c in channel if c.isalnum() or c in "-_.")
        return os.path.join(self.msg_dir, f"{safe}.json")

    def upsert_messages(self, channel: str, msgs: list) -> int:
        """افزودن/به‌روزرسانی پیام‌ها (بدون تکرار) و برگرداندن تعداد پیام‌های جدید."""
        if not msgs:
            return 0
        with self._lock:
            path = self._msg_path(channel)
            existing = {}
            if os.path.exists(path):
                try:
                    with open(path, encoding="utf-8") as f:
                        existing = {str(m["id"]): m for m in json.load(f)}
                except Exception:
                    existing = {}
            new_count = 0
            for m in msgs:
                mid = str(m.get("id"))
                if mid and mid not in existing:
                    new_count += 1
                if mid:
                    existing[mid] = m
            merged = sorted(existing.values(), key=lambda m: int(m["id"]), reverse=True)
            max_stored = self.get_settings().get("max_stored", 2000)
            merged = merged[: max(1, int(max_stored))]
            with open(path, "w", encoding="utf-8") as f:
                json.dump(merged, f, ensure_ascii=False)
            with self._connect() as conn:
                for m in msgs:
                    conn.execute("""
                        INSERT INTO messages
                        (channel, message_id, date, title, description, category,
                         media_type, media_name, media_path, source_link, payload_json, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                        ON CONFLICT(channel, message_id) DO UPDATE SET
                          date=excluded.date, title=excluded.title, description=excluded.description,
                          category=excluded.category, media_type=excluded.media_type,
                          media_name=excluded.media_name, media_path=excluded.media_path,
                          source_link=excluded.source_link, payload_json=excluded.payload_json,
                          updated_at=CURRENT_TIMESTAMP
                    """, (str(channel), str(m.get("id")), m.get("date"), m.get("title"),
                          m.get("description") or m.get("text"), m.get("category"),
                          m.get("has_media") if isinstance(m.get("has_media"), str) else None,
                          m.get("media_name"), m.get("media_path"), m.get("link"),
                          json.dumps(m, ensure_ascii=False)))
            return new_count

    def get_messages(self, channel: str, limit: int = 50) -> list:
        with self._lock:
            try:
                with self._connect() as conn:
                    rows = conn.execute(
                        "SELECT payload_json FROM messages WHERE channel=? "
                        "ORDER BY CAST(message_id AS INTEGER) DESC LIMIT ?",
                        (str(channel), max(1, int(limit)))).fetchall()
                if rows:
                    return [json.loads(row["payload_json"]) for row in rows]
            except Exception:
                pass
            path = self._msg_path(channel)
            if not os.path.exists(path):
                return []
            try:
                with open(path, encoding="utf-8") as f:
                    msgs = json.load(f)
                return msgs[: max(1, int(limit))]
            except Exception:
                return []

    def count(self, channel: str) -> int:
        return len(self.get_messages(channel, limit=10**9))

    def latest_id(self, channel: str):
        msgs = self.get_messages(channel, limit=1)
        return int(msgs[0]["id"]) if msgs else None