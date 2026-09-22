import os
import json
import hashlib
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlsplit
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_IDS = [c.strip() for c in os.getenv("TELEGRAM_CHAT_IDS", "").split(",") if c.strip()]
NEWS_URL = "https://sc-st.univ-batna2.dz/news"
SEEN_FILE = "last_seen.json"
BASE_URL = "https://sc-st.univ-batna2.dz"


def fetch_news():
    resp = requests.get(NEWS_URL, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    items = {}
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/news/" not in href or "/archive/" in href:
            continue

        if href.startswith("/"):
            full_link = BASE_URL + href
        elif href.startswith(BASE_URL):
            full_link = href
        else:
            continue

        if full_link not in items:
            items[full_link] = {"title": None, "image": None}

        img = a.find("img")
        if img and img.get("src") and not items[full_link]["image"]:
            src = img["src"]
            if src.startswith("/"):
                src = BASE_URL + src
            items[full_link]["image"] = src

        text = a.get_text(strip=True)
        if text and not text.lower().startswith("read more") and not items[full_link]["title"]:
            items[full_link]["title"] = text

    return [
        {"title": data["title"], "link": link, "image": data["image"]}
        for link, data in items.items()
        if data["title"]
    ]


def find_pdf_links(article_url):
    resp = requests.get(article_url, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    pdfs = []
    seen_base = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if ".pdf" in href.lower():
            if href.startswith("/"):
                href = BASE_URL + href
            base = urlsplit(href)._replace(query="").geturl()
            if base not in seen_base:
                seen_base.add(base)
                pdfs.append(href)
    return pdfs


def content_hash(title, pdf_links):
    raw = title + "|" + "|".join(sorted(pdf_links))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            # Old format (flat list of links) — migrate to dict, hash unknown yet
            return {link: None for link in data}
        return data
    return {}


def save_seen(seen):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(seen, f, ensure_ascii=False, indent=2)


def send_telegram_post(title, link, image_url=None, updated=False):
    keyboard = {"inline_keyboard": [[{"text": "🔗 Open Post", "url": link}]]}
    label = "🔄 <b>Updated post</b>" if updated else "📢 <b>New post</b>"
    caption = f"{label}\n\n{title}"[:1024]

    if image_url:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
        for chat_id in CHAT_IDS:
            resp = requests.post(url, data={
                "chat_id": chat_id,
                "photo": image_url,
                "caption": caption,
                "parse_mode": "HTML",
                "reply_markup": json.dumps(keyboard),
            })
            if not resp.ok:
                print(f"Failed to send photo to {chat_id}: {resp.text}")
    else:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        for chat_id in CHAT_IDS:
            resp = requests.post(url, data={
                "chat_id": chat_id,
                "text": caption,
                "parse_mode": "HTML",
                "reply_markup": json.dumps(keyboard),
            })
            if not resp.ok:
                print(f"Failed to send text to {chat_id}: {resp.text}")


def send_telegram_pdf(pdf_url, caption=""):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument"
    for chat_id in CHAT_IDS:
        resp = requests.post(url, data={
            "chat_id": chat_id,
            "document": pdf_url,
            "caption": f"📄 {caption}"[:1024],
        })
        if not resp.ok:
            print(f"Failed to send PDF to {chat_id}: {resp.text}")


def main():
    if not CHAT_IDS:
        print("No chat IDs configured. Set TELEGRAM_CHAT_IDS in .env")
        return

    news = fetch_news()
    print(f"Found {len(news)} items")

    seen = load_seen()

    to_send = []  # (item, pdf_links, is_update)

    for item in news:
        link = item["link"]
        try:
            pdf_links = find_pdf_links(link)
        except Exception as e:
            print(f"Couldn't fetch PDFs for {link}: {e}")
            pdf_links = []

        h = content_hash(item["title"], pdf_links)

        if link not in seen:
            to_send.append((item, pdf_links, False))
        elif seen[link] is None:
            pass  # migrated entry — silently backfill hash, no notification
        elif seen[link] != h:
            to_send.append((item, pdf_links, True))

        seen[link] = h

    if to_send:
        for item, pdf_links, is_update in reversed(to_send):
            send_telegram_post(item["title"], item["link"], item.get("image"), updated=is_update)
            for pdf_url in pdf_links:
                send_telegram_pdf(pdf_url, caption=item["title"])

        save_seen(seen)
        print(f"Sent {len(to_send)} item(s) ({sum(1 for _, _, u in to_send if u)} updates).")
    else:
        save_seen(seen)
        print("No new items.")


if __name__ == "__main__":
    main()
