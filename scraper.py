"""موتور وب: خواندن پیام‌های کانال عمومی از پیش‌نمایش وب t.me/s/<channel>
بدون نیاز به api_id — هر صفحه ~۲۰ پیام می‌دهد و با before به عقب صفحه‌بندی می‌شود.
مسیر اتصال مستقیم یا از طریق رله فعال‌شده در پنل مدیریت (پروکسی یا آدرس پایه سفارشی) است."""
import time
import requests
from bs4 import BeautifulSoup

import tg_client

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept-Language": "en",
}


def parse_html(html: str) -> list:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for m in soup.select("div.tgme_widget_message"):
        a = m.select_one("a.tgme_widget_message_date")
        t = m.select_one(".tgme_widget_message_text")
        tm = m.select_one("time")
        views = m.select_one("span.tgme_widget_message_views")
        link = a["href"] if a else None
        out.append({
            "id": link.split("/")[-1] if link else None,
            "link": link,
            "text": t.get_text(" ", strip=True) if t else "",
            "date": tm.get("datetime") if tm else None,
            "views": views.get_text(strip=True) if views else None,
            "has_media": bool(m.select_one(".tgme_widget_message_photo_wrap, "
                                           ".tgme_widget_message_video_wrap")),
        })
    return out


def fetch_page(channel: str, before=None) -> list:
    url = tg_client.telegram_web_base() + f"/s/{channel}" + \
          (f"?before={before}" if before else "")
    r = requests.get(url, headers=HEADERS, timeout=20,
                     proxies=tg_client.proxies_for_requests())
    if r.status_code == 404:
        raise RuntimeError(f"کانال @{channel} در وب یافت نشد (خصوصی است یا وجود ندارد).")
    r.raise_for_status()
    return parse_html(r.text)


def fetch_new(channel: str, max_pages: int = 5, min_id: int = 0) -> list:
    """جمع‌آوری پیام‌های جدیدتر از min_id با حداکثر max_pages صفحه."""
    collected, before = [], None
    for _ in range(max(1, int(max_pages))):
        page = fetch_page(channel, before)
        if not page:
            break
        collected.extend(page)
        # قدیمی‌ترین پیام صفحه، نقطه ادامه صفحه‌بندی به عقب است.
        ids = [int(m["id"]) for m in page if m["id"]]
        if not ids:
            break
        oldest = min(ids)
        if oldest <= min_id or oldest <= 1:
            break
        before = oldest
        time.sleep(1)  # احترام به محدودیت نرخ
    seen, unique = set(), []
    for m in collected:
        if m["id"] and m["id"] not in seen:
            seen.add(m["id"])
            unique.append(m)
    if min_id:
        unique = [m for m in unique if int(m["id"]) > min_id]
    return unique


def fetch_history(channel: str, max_messages: int = 100) -> list:
    """دریافت ~آخرین max_messages پیام با صفحه‌بندی به عقب."""
    all_msgs, before = [], None
    while len(all_msgs) < max_messages:
        page = fetch_page(channel, before)
        if not page:
            break
        all_msgs.extend(page)
        before = int(page[0]["id"]) if page[0]["id"] else None
        if not before:
            break
        time.sleep(1)
    seen, unique = set(), []
    for m in all_msgs:
        if m["id"] and m["id"] not in seen:
            seen.add(m["id"])
            unique.append(m)
    return unique