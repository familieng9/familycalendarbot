"""
kids_activities_bot.py - Scrapes SG kids activities and sends to WhatsApp group

Scrapes SassyMama & SunnyCityKids every Thursday, formats 3 messages,
and sends them sequentially to the family WhatsApp group.

Required GitHub Secrets (shared with family_bot_cloud.py):
  SESSION_PASSWORD   : Password used to encrypt session_encrypted.zip
  GOOGLE_CREDENTIALS : Full contents of credentials.json (not used but kept consistent)
  GOOGLE_TOKEN       : Full contents of token.json (not used but kept consistent)
"""

import asyncio
import datetime
import logging
import os
import re
import shutil
import sys
import urllib.request
from html.parser import HTMLParser

import pyzipper
from playwright.async_api import async_playwright

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

GROUP_INVITE_CODE = "FHQ7HrFjHEOJQ3fbnl84UC"
SESSION_DIR       = "session_data"
SESSION_ZIP       = "session_encrypted.zip"

SASSYMAMA_URL     = "https://www.sassymamasg.com/play-weekend-planner-fun-activities-events-kids/"
SUNNYCITY_URL     = "https://sunnycitykids.com/blog/kids-activities-this-week-in-singapore"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def get_upcoming_weekend():
    today = datetime.date.today()
    days_until_sat = (5 - today.weekday() + 7) % 7 or 7
    sat = today + datetime.timedelta(days=days_until_sat)
    sun = sat + datetime.timedelta(days=1)
    fmt = lambda d: d.strftime("%-d %b %Y") if sys.platform != "win32" else d.strftime("%d %b %Y").lstrip("0")
    return {"sat": fmt(sat), "sun": fmt(sun)}

def get_today_formatted():
    today = datetime.date.today()
    return today.strftime("%d %b %Y").lstrip("0")

# ---------------------------------------------------------------------------
# Simple HTML fetcher + text extractor
# ---------------------------------------------------------------------------

def fetch_page(url: str) -> str:
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="replace")


class TextExtractor(HTMLParser):
    """Minimal HTML -> plain text extractor."""
    def __init__(self):
        super().__init__()
        self.text_parts = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self._skip = True
        if tag in ("h2", "h3", "p", "li", "br"):
            self.text_parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript"):
            self._skip = False

    def handle_data(self, data):
        if not self._skip:
            self.text_parts.append(data)

    def get_text(self):
        return "".join(self.text_parts)


def html_to_text(html: str) -> str:
    p = TextExtractor()
    p.feed(html)
    return p.get_text()

# ---------------------------------------------------------------------------
# Scrapers
# ---------------------------------------------------------------------------

def scrape_sassymama() -> list:
    log.info("Scraping SassyMama...")
    try:
        html  = fetch_page(SASSYMAMA_URL)
        text  = html_to_text(html)
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        activities = []

        for i, line in enumerate(lines):
            # Heuristic: lines that look like activity titles (title case, 10-80 chars)
            if len(line) < 10 or len(line) > 80:
                continue
            if not re.search(r"[A-Z]", line):
                continue
            if any(skip in line.lower() for skip in ["sassy mama", "subscribe", "cookie", "privacy", "advertisement"]):
                continue

            # Grab surrounding context as details
            context = " ".join(lines[i+1:i+5])
            is_free  = bool(re.search(r"free|complimentary", line + context, re.I))
            cost_m   = re.search(r"\$[\d,]+", context)
            cost     = "FREE" if is_free else (cost_m.group(0) if cost_m else "See details")
            date_m   = re.search(r"\d{1,2}\s+\w{3}[\w\s,–-]*\d{4}", context)
            when     = date_m.group(0).strip() if date_m else "This weekend"
            url_m    = re.search(r'href="(https?://[^"]+)"', html[max(0, html.find(line)-200):html.find(line)+500])
            src_url  = url_m.group(1) if url_m else SASSYMAMA_URL

            activities.append({
                "title": line, "cost": cost, "when": when,
                "sourceUrl": src_url, "sourceName": "SassyMama",
                "isFree": is_free or (cost_m and int(re.sub(r"\D", "", cost_m.group(0)) or 0) <= 25),
            })

        log.info(f"  → {len(activities)} items from SassyMama")
        return activities[:20]
    except Exception as e:
        log.warning(f"SassyMama scrape failed: {e}")
        return []


def scrape_sunnycitykids() -> list:
    log.info("Scraping SunnyCityKids...")
    try:
        html  = fetch_page(SUNNYCITY_URL)
        text  = html_to_text(html)
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        activities = []

        for i, line in enumerate(lines):
            if len(line) < 10 or len(line) > 80:
                continue
            if not re.search(r"[A-Z]", line):
                continue
            if any(skip in line.lower() for skip in ["sunny city", "subscribe", "cookie", "privacy", "advertisement"]):
                continue

            context  = " ".join(lines[i+1:i+5])
            is_free  = bool(re.search(r"free", line + context, re.I))
            cost_m   = re.search(r"\$[\d,]+", context)
            cost     = "FREE" if is_free else (cost_m.group(0) if cost_m else "See details")
            age_m    = re.search(r"(?:ages?|kids?)[:\s]*([\d][\d\s–-]*(?:years?|months?|\+)?)", context, re.I)
            ages     = age_m.group(1).strip() if age_m else ""
            when_m   = re.search(r"\d{1,2}\s+\w{3}[\w\s,–-]*(?:\d{4})?", context)
            when     = when_m.group(0).strip() if when_m else "This weekend"
            url_m    = re.search(r'href="(https?://[^"]+)"', html[max(0, html.find(line)-200):html.find(line)+500])
            src_url  = url_m.group(1) if url_m else SUNNYCITY_URL

            activities.append({
                "title": line, "cost": cost, "when": when, "ages": ages,
                "sourceUrl": src_url, "sourceName": "SunnyCityKids",
                "isFree": is_free or (cost_m and int(re.sub(r"\D", "", cost_m.group(0)) or 0) <= 25),
            })

        log.info(f"  → {len(activities)} items from SunnyCityKids")
        return activities[:20]
    except Exception as e:
        log.warning(f"SunnyCityKids scrape failed: {e}")
        return []

# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def normalise(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())[:30]

def deduplicate(items: list) -> list:
    seen = set()
    out  = []
    for item in items:
        key = normalise(item["title"])
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out

# ---------------------------------------------------------------------------
# WhatsApp message formatting (plain text)
# ---------------------------------------------------------------------------

def format_activity(item: dict, index: int) -> str:
    lines = [f"{index}. {item['title']}"]
    lines.append(f"   Cost: {item.get('cost', 'See details')}")
    if item.get("when"):
        lines.append(f"   When: {item['when']}")
    if item.get("ages"):
        lines.append(f"   Ages: {item['ages']}")
    lines.append(f"   Source: {item['sourceName']} - {item['sourceUrl']}")
    return "\n".join(lines)


def build_messages(activities: list, weekend: dict) -> list:
    budget   = [a for a in activities if a.get("isFree")][:14]
    ticketed = [a for a in activities if not a.get("isFree")][:5]

    if len(budget) < 7:
        extra = [a for a in activities if a not in budget][:7 - len(budget)]
        budget.extend(extra)

    header = f"🗓 SG Kids Activities | Weekend of {weekend['sat']} - {weekend['sun']}\nCurated from SassyMama & SunnyCityKids | FREE or under $25"

    msg1_body = "\n\n".join(format_activity(a, i+1) for i, a in enumerate(budget[:7]))
    msg1 = f"{header}\n\n⭐ THIS WEEKEND'S ACTIVITIES (FREE or <$25)\n\n{msg1_body or 'No activities found this week — check sources manually.'}"

    msg2_body = "\n\n".join(format_activity(a, i+8) for i, a in enumerate(budget[7:14]))
    msg2 = f"⭐ MORE FREE & UNDER $25 ACTIVITIES (continued)\n\n{msg2_body or 'No additional activities found.'}"

    msg3_body = "\n\n".join(format_activity(a, i+1) for i, a in enumerate(ticketed))
    msg3 = f"📢 MAJOR EVENTS - OPEN FOR REGISTRATION\n\n{msg3_body or 'No major ticketed events found this week.'}\n\nUpdated: {get_today_formatted()}"

    return [msg1, msg2, msg3]

# ---------------------------------------------------------------------------
# WhatsApp session restore
# ---------------------------------------------------------------------------

def restore_whatsapp_session():
    password = os.environ.get("SESSION_PASSWORD")
    if not password:
        raise EnvironmentError("SESSION_PASSWORD environment variable is not set.")
    if not os.path.exists(SESSION_ZIP):
        raise FileNotFoundError(f"{SESSION_ZIP} not found in repo.")

    if os.path.exists(SESSION_DIR):
        shutil.rmtree(SESSION_DIR)
    os.makedirs(SESSION_DIR, exist_ok=True)

    log.info(f"Decrypting {SESSION_ZIP} -> {SESSION_DIR}/")
    with pyzipper.AESZipFile(SESSION_ZIP, "r") as zf:
        zf.setpassword(password.encode())
        for zip_info in zf.infolist():
            normalised = zip_info.filename.replace("\\", "/")
            dest = os.path.join(SESSION_DIR, normalised)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            if not normalised.endswith("/"):
                with zf.open(zip_info) as src, open(dest, "wb") as dst:
                    dst.write(src.read())
    log.info("Session restored successfully.")

# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

async def dump_page_state(page, label: str):
    path = f"debug_{label}.png"
    await page.screenshot(path=path, full_page=True)
    log.info(f"Screenshot saved: {path}")

# ---------------------------------------------------------------------------
# WhatsApp sending
# ---------------------------------------------------------------------------

async def send_whatsapp_messages(messages: list):
    restore_whatsapp_session()

    async with async_playwright() as p:
        log.info("Launching headless Chromium...")
        browser = await p.chromium.launch_persistent_context(
            user_data_dir=SESSION_DIR,
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--disable-gpu",
                "--window-size=1920,1080",
                "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            ],
        )

        page = browser.pages[0] if browser.pages else await browser.new_page()
        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
            window.chrome = { runtime: {} };
        """)

        try:
            log.info("Loading WhatsApp Web...")
            await page.goto("https://web.whatsapp.com", timeout=60000, wait_until="domcontentloaded")

            log.info("Waiting for WhatsApp sidebar (up to 90s)...")
            try:
                await page.wait_for_selector(
                    '[data-testid="chat-list"], [aria-label="Chat list"], ._aigw, #pane-side',
                    timeout=90000
                )
                log.info("Chat list loaded.")
            except Exception:
                await dump_page_state(page, "01_no_chatlist")
                raise RuntimeError("WhatsApp session invalid. Re-run login_exporter.py and recommit session_encrypted.zip.")

            await asyncio.sleep(5)

            # Navigate to group via invite URL
            group_url = f"https://web.whatsapp.com/accept?code={GROUP_INVITE_CODE}"
            log.info(f"Navigating to group: {group_url}")
            await page.goto(group_url, timeout=30000, wait_until="domcontentloaded")
            await asyncio.sleep(5)
            await dump_page_state(page, "02_after_invite")

            # Find compose box
            compose_selector = (
                'div[contenteditable="true"][data-tab="10"], '
                'footer div[contenteditable="true"], '
                '[data-testid="conversation-compose-box-input"]'
            )

            try:
                await page.wait_for_selector(compose_selector, timeout=15000)
                log.info("Compose box ready.")
            except Exception:
                await dump_page_state(page, "03_no_compose")
                raise RuntimeError("Could not reach compose box.")

            compose = page.locator(compose_selector).first

            # Send each message
            for i, message in enumerate(messages, 1):
                log.info(f"Sending message {i} of {len(messages)}...")
                await compose.click()
                await asyncio.sleep(0.5)

                lines = message.split("\n")
                for j, line in enumerate(lines):
                    await compose.type(line, delay=10)
                    if j < len(lines) - 1:
                        await page.keyboard.press("Shift+Enter")

                await page.keyboard.press("Enter")
                log.info(f"Message {i} sent.")
                await asyncio.sleep(3)  # pause between messages

        except Exception as e:
            log.error(f"Failed to send messages: {e}")
            raise
        finally:
            await browser.close()
            log.info("Browser closed.")

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    log.info("=== SG Kids Activities Bot Starting ===")
    try:
        weekend    = get_upcoming_weekend()
        log.info(f"Weekend: {weekend['sat']} - {weekend['sun']}")

        sassymama  = scrape_sassymama()
        sunnycity  = scrape_sunnycitykids()
        all_items  = deduplicate(sassymama + sunnycity)

        log.info(f"Total unique activities: {len(all_items)}")

        if not all_items:
            log.warning("No activities found. Sending fallback message.")
            all_items = [{
                "title": "No activities found this week",
                "cost": "N/A", "when": "This weekend",
                "sourceUrl": SASSYMAMA_URL, "sourceName": "SassyMama", "isFree": True,
            }]

        messages = build_messages(all_items, weekend)
        asyncio.run(send_whatsapp_messages(messages))
        log.info("=== Kids Activities Bot finished successfully ===")

    except Exception as e:
        log.error(f"=== Bot failed: {e} ===")
        sys.exit(1)


if __name__ == "__main__":
    main()
