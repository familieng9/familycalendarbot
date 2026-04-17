"""
kids_activities_bot.py - Scrapes SG kids activities and sends to WhatsApp group

Scrapes SassyMama & SunnyCityKids every Thursday, formats 3 messages,
and sends them sequentially to the family WhatsApp group.

Required GitHub Secrets:
  SESSION_PASSWORD : Password used to encrypt session_encrypted.zip
"""

import asyncio
import datetime
import logging
import os
import re
import shutil
import sys
import urllib.request

import pyzipper
from playwright.async_api import async_playwright

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

GROUP_INVITE_CODE = "FHQ7HrFjHEOJQ3fbnl84UC"
SESSION_DIR       = "session_data"
SESSION_ZIP       = "session_encrypted.zip"

SASSYMAMA_URL = "https://www.sassymamasg.com/play-weekend-planner-fun-activities-events-kids/"
SUNNYCITY_URL = "https://www.sunnycitykids.com/blog/kids-activities-this-week-in-singapore"

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
    def fmt(d):
        return d.strftime("%d %b %Y").lstrip("0")
    return {"sat": fmt(sat), "sun": fmt(sun)}

def get_today_formatted():
    return datetime.date.today().strftime("%d %b %Y").lstrip("0")

# ---------------------------------------------------------------------------
# Fetch HTML
# ---------------------------------------------------------------------------

def fetch_html(url: str) -> str:
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="replace")

def strip_tags(html: str) -> str:
    return re.sub(r'<[^>]+>', '', html)

# ---------------------------------------------------------------------------
# SunnyCityKids scraper
# Structure: ### N. Event Name ... **Date:** ... **Admission:**/**Tickets:**/**Price:**
# ---------------------------------------------------------------------------

def scrape_sunnycitykids() -> list:
    log.info("Scraping SunnyCityKids...")
    try:
        html = fetch_html(SUNNYCITY_URL)

        # Find the Featured Activities section
        # Events are <h3> tags like: <h3>1. <strong>PLAYtime!</strong></h3>
        # followed by content blocks with **Date:** **Admission:** etc.

        activities = []

        # Split on h3 tags to get event blocks
        # Pattern: <h3>N. EventName</h3> ... next <h3>
        blocks = re.split(r'<h3[^>]*>', html)

        for block in blocks[1:]:  # skip everything before first h3
            # Extract heading text
            heading_match = re.match(r'(.*?)</h3>', block, re.DOTALL)
            if not heading_match:
                continue

            heading_raw = heading_match.group(1)
            heading = strip_tags(heading_raw).strip()

            # Must start with a number (e.g. "1. PLAYtime!")
            if not re.match(r'^\d+\.', heading):
                continue

            # Remove leading number
            title = re.sub(r'^\d+\.\s*', '', heading).strip()
            if not title or len(title) < 3:
                continue

            # Skip table-of-contents duplicates (short blocks with no date info)
            block_text = strip_tags(block)

            # Extract date
            date_match = re.search(r'\*\*Date[s]?:\*\*\s*([^\n*]{3,80})', block)
            if not date_match:
                date_match = re.search(r'Date[s]?:\s*([^\n<]{3,80})', block_text)
            date = date_match.group(1).strip() if date_match else "This weekend"
            date = date.split('\n')[0].strip()

            # Extract cost - check Admission, Tickets, Price fields
            cost = "See details"
            for field in [r'\*\*Admission:\*\*', r'\*\*Tickets?:\*\*', r'\*\*Price[s]?:\*\*', r'\*\*Rates?:\*\*']:
                cost_match = re.search(field + r'\s*([^\n*]{2,80})', block)
                if cost_match:
                    cost_raw = strip_tags(cost_match.group(1)).strip()
                    cost = cost_raw.split('\n')[0].strip()
                    break

            # Check for free
            if re.search(r'\bfree\b', cost, re.I) or re.search(r'\bfree\b', block_text[:500], re.I):
                if cost == "See details":
                    cost = "Free"

            # Extract link - prefer sunnycitykids.com/latest/ or sunnycitykids.com/activities/
            link = SUNNYCITY_URL
            link_match = re.search(r'href="(https://www\.sunnycitykids\.com/(?:latest|activities)/[^"]+)"', block)
            if link_match:
                link = link_match.group(1)
            else:
                # Try any non-ad external link
                ext_match = re.search(r'href="(https://(?!ad\.doubleclick|www\.instagram|www\.klook)[^"]+)"', block[:1000])
                if ext_match:
                    link = ext_match.group(1)

            # is_free: free or cost mentions $25 or less
            is_free = bool(re.search(r'\bfree\b', cost + block_text[:300], re.I))
            if not is_free:
                prices = re.findall(r'\$(\d+)', cost)
                if prices and all(int(p) <= 25 for p in prices):
                    is_free = True

            activities.append({
                "title":      title,
                "date":       date,
                "cost":       cost if cost else "See details",
                "link":       link,
                "sourceName": "SunnyCityKids",
                "isFree":     is_free,
            })

        # Dedupe by title within this source (table of contents appears twice)
        seen = set()
        unique = []
        for a in activities:
            key = re.sub(r'[^a-z0-9]', '', a['title'].lower())[:25]
            if key not in seen:
                seen.add(key)
                unique.append(a)

        log.info(f"  → {len(unique)} events from SunnyCityKids")
        return unique

    except Exception as e:
        log.warning(f"SunnyCityKids failed: {e}")
        return []


# ---------------------------------------------------------------------------
# SassyMama scraper
# Structure: numbered H2 headings in article body, paragraphs with date/cost
# ---------------------------------------------------------------------------

def scrape_sassymama() -> list:
    log.info("Scraping SassyMama...")
    try:
        html = fetch_html(SASSYMAMA_URL)

        # Find article body - content between entry-content or post-content divs
        # SassyMama wraps article in <div class="entry-content"> or similar
        article_match = re.search(
            r'<(?:div|article)[^>]*class="[^"]*(?:entry-content|post-content|article-content)[^"]*"[^>]*>(.*?)</(?:div|article)>',
            html, re.DOTALL | re.I
        )

        if article_match:
            article_html = article_match.group(1)
        else:
            # Fallback: find content between first h1 and footer
            h1_pos = html.find('<h1')
            footer_pos = html.find('<footer')
            if h1_pos > 0 and footer_pos > h1_pos:
                article_html = html[h1_pos:footer_pos]
            else:
                article_html = html

        activities = []

        # Split on h2 tags - SassyMama uses numbered H2s for each event
        blocks = re.split(r'<h2[^>]*>', article_html)

        for block in blocks[1:]:
            heading_match = re.match(r'(.*?)</h2>', block, re.DOTALL)
            if not heading_match:
                continue

            heading_raw = heading_match.group(1)
            heading = strip_tags(heading_raw).strip()

            # Must start with a number
            if not re.match(r'^\d+[\.\)]\s*', heading):
                continue

            title = re.sub(r'^\d+[\.\)]\s*', '', heading).strip()
            if not title or len(title) < 5:
                continue

            block_text = strip_tags(block[:2000])

            # Extract date
            date_match = re.search(
                r'(?:date|when|dates?)[:\s]+([^\n]{5,60})',
                block_text, re.I
            )
            if not date_match:
                # Look for date patterns directly
                date_match = re.search(
                    r'(\d{1,2}\s+\w{3,9}(?:\s*[-–]\s*\d{1,2}\s+\w{3,9})?\s*\d{4})',
                    block_text
                )
            date = date_match.group(1).strip() if date_match else "This weekend"
            date = date.split('\n')[0].strip()[:60]

            # Extract cost
            cost = "See details"
            cost_match = re.search(
                r'(?:cost|price|admission|tickets?|fee)[:\s]+([^\n]{2,60})',
                block_text, re.I
            )
            if cost_match:
                cost = cost_match.group(1).strip()
            elif re.search(r'\bfree\b', block_text[:500], re.I):
                cost = "Free"
            else:
                dollar_match = re.search(r'(\$[\d,]+(?:\s*[-–]\s*\$[\d,]+)?)', block_text)
                if dollar_match:
                    cost = dollar_match.group(1)

            # Extract link
            link = SASSYMAMA_URL
            link_match = re.search(r'href="(https://www\.sassymamasg\.com/[^"]+)"', block[:1500])
            if link_match:
                candidate = link_match.group(1)
                # Skip category/nav links
                if not any(skip in candidate for skip in ['/category/', '/tag/', '/author/']):
                    link = candidate

            # is_free
            is_free = bool(re.search(r'\bfree\b', cost + block_text[:300], re.I))
            if not is_free:
                prices = re.findall(r'\$(\d+)', cost)
                if prices and all(int(p) <= 25 for p in prices):
                    is_free = True

            activities.append({
                "title":      title,
                "date":       date,
                "cost":       cost,
                "link":       link,
                "sourceName": "SassyMama",
                "isFree":     is_free,
            })

        log.info(f"  → {len(activities)} events from SassyMama")
        return activities

    except Exception as e:
        log.warning(f"SassyMama failed: {e}")
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
# Message formatting
# ---------------------------------------------------------------------------

def format_activity(item: dict, index: int) -> str:
    lines = [f"{index}. {item['title']}"]
    lines.append(f"   📅 {item['date']}")
    lines.append(f"   💰 {item['cost']}")
    lines.append(f"   🔗 {item['link']}")
    return "\n".join(lines)


def build_messages(activities: list, weekend: dict) -> list:
    free_events     = [a for a in activities if a.get("isFree")]
    ticketed_events = [a for a in activities if not a.get("isFree")]

    # Pad free list if short
    if len(free_events) < 7:
        extra = [a for a in ticketed_events if a not in free_events][:7 - len(free_events)]
        free_events.extend(extra)

    header = (
        f"🗓 SG Kids Activities | {weekend['sat']} - {weekend['sun']}\n"
        f"Source: SassyMama & SunnyCityKids"
    )

    msg1_items = "\n\n".join(format_activity(a, i+1) for i, a in enumerate(free_events[:7]))
    msg1 = f"{header}\n\n⭐ FREE & UNDER $25\n\n{msg1_items or 'No free activities found this week.'}"

    msg2_items = "\n\n".join(format_activity(a, i+8) for i, a in enumerate(free_events[7:14]))
    msg2 = f"⭐ MORE FREE & UNDER $25\n\n{msg2_items or 'No additional free activities found.'}"

    msg3_items = "\n\n".join(format_activity(a, i+1) for i, a in enumerate(ticketed_events[:8]))
    msg3 = (
        f"🎟 TICKETED EVENTS\n\n"
        f"{msg3_items or 'No ticketed events found this week.'}\n\n"
        f"Updated: {get_today_formatted()}"
    )

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

            group_url = f"https://web.whatsapp.com/accept?code={GROUP_INVITE_CODE}"
            log.info(f"Navigating to group: {group_url}")
            await page.goto(group_url, timeout=30000, wait_until="domcontentloaded")
            await asyncio.sleep(5)
            await dump_page_state(page, "02_after_invite")

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
                await asyncio.sleep(3)

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
        weekend   = get_upcoming_weekend()
        log.info(f"Weekend: {weekend['sat']} - {weekend['sun']}")

        sunnycity  = scrape_sunnycitykids()
        sassymama  = scrape_sassymama()
        all_items  = deduplicate(sunnycity + sassymama)  # SunnyCityKids first (better data)

        log.info(f"Total unique activities: {len(all_items)}")

        if not all_items:
            log.warning("No activities found. Sending fallback message.")
            fallback = [(
                f"⚠️ Kids Activities scraper found nothing this week.\n"
                f"Check manually:\n• {SASSYMAMA_URL}\n• {SUNNYCITY_URL}"
            )]
            asyncio.run(send_whatsapp_messages(fallback))
            return

        messages = build_messages(all_items, weekend)
        asyncio.run(send_whatsapp_messages(messages))
        log.info("=== Kids Activities Bot finished successfully ===")

    except Exception as e:
        log.error(f"=== Bot failed: {e} ===")
        sys.exit(1)


if __name__ == "__main__":
    main()
