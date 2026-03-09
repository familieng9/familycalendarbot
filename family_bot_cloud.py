"""
family_bot_cloud.py  -  Cloud automation script for GitHub Actions

Session storage approach:
  - session_encrypted.zip is committed to the repo (AES-256 encrypted)
  - SESSION_PASSWORD secret decrypts it at runtime
  - GOOGLE_CREDENTIALS and GOOGLE_TOKEN handle Calendar auth

Required GitHub Secrets:
  SESSION_PASSWORD   : Password used to encrypt session_encrypted.zip
  GOOGLE_CREDENTIALS : Full contents of credentials.json
  GOOGLE_TOKEN       : Full contents of token.json
"""

import asyncio
import datetime
import logging
import os
import shutil
import sys

import pytz
import pyzipper
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from playwright.async_api import async_playwright

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

GROUP_INVITE_CODE = "FHQ7HrFjHEOJQ3fbnl84UC"

CALENDAR_IDS = [
    "primary",
    "0gs624o1448ja48f0ielplj9co@group.calendar.google.com",
    "family07313615549286623759@group.calendar.google.com",
]

SESSION_DIR = "session_data"
SESSION_ZIP = "session_encrypted.zip"
SG_TZ       = pytz.timezone("Asia/Singapore")

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
# Google Calendar
# ---------------------------------------------------------------------------

def restore_google_credentials() -> Credentials:
    credentials_json = os.environ.get("GOOGLE_CREDENTIALS")
    token_json       = os.environ.get("GOOGLE_TOKEN")
    if not credentials_json or not token_json:
        raise EnvironmentError("GOOGLE_CREDENTIALS and GOOGLE_TOKEN must be set.")
    with open("credentials.json", "w") as f:
        f.write(credentials_json)
    with open("token.json", "w") as f:
        f.write(token_json)
    return Credentials.from_authorized_user_file("token.json")


def get_tomorrow_events() -> list:
    creds   = restore_google_credentials()
    service = build("calendar", "v3", credentials=creds)

    now_sg        = datetime.datetime.now(SG_TZ)
    tomorrow_date = (now_sg + datetime.timedelta(days=1)).date()
    start = SG_TZ.localize(datetime.datetime.combine(tomorrow_date, datetime.time.min)).isoformat()
    end   = SG_TZ.localize(datetime.datetime.combine(tomorrow_date, datetime.time.max)).isoformat()

    log.info(f"Fetching events for {tomorrow_date} (SGT)")
    all_events = []
    for cal_id in CALENDAR_IDS:
        try:
            result = service.events().list(
                calendarId=cal_id, timeMin=start, timeMax=end,
                singleEvents=True, orderBy="startTime"
            ).execute()
            for event in result.get("items", []):
                raw_start = event["start"].get("dateTime", event["start"].get("date"))
                if "T" in raw_start:
                    event_dt = datetime.datetime.fromisoformat(raw_start).astimezone(SG_TZ)
                    time_str = event_dt.strftime("%I:%M %p")
                else:
                    time_str = "All Day"
                all_events.append(f"• {time_str}: {event['summary']}")
        except Exception as e:
            log.error(f"Error fetching calendar {cal_id}: {e}")
    return all_events


def build_message() -> str:
    events = get_tomorrow_events()
    if not events:
        return "🌙 No activities scheduled for tomorrow!"
    return "📅 Tomorrow's Schedule:\n" + "\n".join(events)


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
# WhatsApp group navigation
# ---------------------------------------------------------------------------

async def open_group_chat(page) -> bool:
    """
    Try multiple strategies to open the group and get to the compose box.
    Strategy 1: Search bar (tries multiple known selectors)
    Strategy 2: Scan the visible chat list for a group title match
    Strategy 3: Navigate via invite URL and handle any dialogs
    """

    # --- Strategy 1: Search bar ---
    search_selectors = [
        '[data-testid="search"]',
        '[data-testid="chat-list-search"]',
        'div[contenteditable="true"][data-tab="3"]',
        'div[contenteditable="true"][title="Search input textbox"]',
        'div[contenteditable="true"][data-lexical-editor="true"]',
        'span[data-icon="search"]',
    ]

    log.info("Strategy 1: trying search bar...")
    for sel in search_selectors:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=3000):
                await el.click()
                await asyncio.sleep(0.5)
                # After clicking a search icon, look for the actual input
                try:
                    inp = page.locator(
                        'div[contenteditable="true"][data-tab="3"], '
                        '[data-testid="search-input"], '
                        'div[contenteditable="true"][role="textbox"]'
                    ).first
                    await inp.wait_for(timeout=3000)
                    await inp.type("Family", delay=50)
                except Exception:
                    await el.type("Family", delay=50)
                await asyncio.sleep(2)

                # Click the first chat result
                result_selectors = [
                    '[data-testid="cell-frame-container"]',
                    '[data-testid="chat-list-item"]',
                    '#pane-side [role="listitem"]',
                    '#pane-side [tabindex="-1"]',
                ]
                for rsel in result_selectors:
                    try:
                        first = page.locator(rsel).first
                        if await first.is_visible(timeout=3000):
                            await first.click()
                            await asyncio.sleep(1)
                            compose = page.locator('[data-testid="conversation-compose-box-input"]')
                            await compose.wait_for(timeout=10000)
                            log.info(f"Search strategy succeeded with selectors: {sel} + {rsel}")
                            return True
                    except Exception:
                        continue

                # Clear search if nothing worked
                await page.keyboard.press("Escape")
                await asyncio.sleep(0.5)
                break
        except Exception:
            continue

    # --- Strategy 2: Scan sidebar for group name ---
    log.info("Strategy 2: scanning sidebar chat list...")
    group_name_candidates = ["Family", "家", "Fam"]
    sidebar_selectors = [
        '[data-testid="cell-frame-container"]',
        '#pane-side [role="listitem"]',
        '#pane-side [tabindex="-1"]',
    ]
    for rsel in sidebar_selectors:
        try:
            items = page.locator(rsel)
            count = await items.count()
            log.info(f"  Found {count} items with selector: {rsel}")
            for i in range(min(count, 20)):
                item = items.nth(i)
                text = await item.inner_text()
                log.info(f"  Chat [{i}]: {text[:60].strip()}")
                for name in group_name_candidates:
                    if name.lower() in text.lower():
                        await item.click()
                        await asyncio.sleep(1)
                        compose = page.locator('[data-testid="conversation-compose-box-input"]')
                        await compose.wait_for(timeout=10000)
                        log.info(f"Found group via sidebar scan: {text[:40]}")
                        return True
        except Exception as e:
            log.info(f"  Sidebar scan error: {e}")
            continue

    # --- Strategy 3: Invite URL with dialog handling ---
    log.info("Strategy 3: trying invite URL with dialog handling...")
    group_url = f"https://web.whatsapp.com/accept?code={GROUP_INVITE_CODE}"
    await page.goto(group_url, timeout=30000, wait_until="domcontentloaded")
    await asyncio.sleep(3)

    # Log all visible buttons/links for debugging
    try:
        btns = page.locator("button, a")
        btn_count = await btns.count()
        for i in range(min(btn_count, 10)):
            btn = btns.nth(i)
            txt = await btn.inner_text()
            log.info(f"  Button/link [{i}]: '{txt.strip()}'")
    except Exception:
        pass

    # Try clicking any "continue" or "open" button
    dialog_selectors = [
        'button:has-text("Continue")',
        'button:has-text("Open")',
        'button:has-text("Join")',
        'a:has-text("Continue")',
        '[data-testid="popup-controls-ok"]',
        '[data-testid="join-btn"]',
    ]
    for sel in dialog_selectors:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=2000):
                log.info(f"Clicking dialog button: {sel}")
                await btn.click()
                await asyncio.sleep(3)
                break
        except Exception:
            continue

    try:
        compose = page.locator('[data-testid="conversation-compose-box-input"]')
        await compose.wait_for(timeout=15000)
        log.info("Strategy 3 (invite URL) succeeded.")
        return True
    except Exception as e:
        log.error(f"Strategy 3 failed: {e}")
        await page.screenshot(path="debug_screenshot.png")
        return False


# ---------------------------------------------------------------------------
# WhatsApp sending
# ---------------------------------------------------------------------------

async def send_whatsapp_message(message: str):
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
                "--window-size=1280,800",
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

            log.info("Waiting for chat list (up to 60s)...")
            try:
                await page.wait_for_selector(
                    '[data-testid="chat-list"], [data-testid="default-user"], #app .two',
                    timeout=60000
                )
                log.info("Session valid. Chat list loaded.")
            except Exception:
                await page.screenshot(path="debug_screenshot.png")
                raise RuntimeError(
                    "WhatsApp session invalid or expired. "
                    "Re-run login_exporter.py and recommit session_encrypted.zip."
                )

            # Log page title and URL for debugging
            log.info(f"Page URL: {page.url}")
            log.info(f"Page title: {await page.title()}")
            await asyncio.sleep(2)

            # Open the group
            if not await open_group_chat(page):
                raise RuntimeError("All strategies to open WhatsApp group failed. Check debug_screenshot.png artifact.")

            # Send the message
            input_box = page.locator('[data-testid="conversation-compose-box-input"]')
            await input_box.click()
            await asyncio.sleep(0.5)

            lines = message.split("\n")
            for i, line in enumerate(lines):
                await input_box.type(line, delay=20)
                if i < len(lines) - 1:
                    await page.keyboard.press("Shift+Enter")

            await page.keyboard.press("Enter")
            log.info("Message sent successfully!")
            await asyncio.sleep(4)

        except Exception as e:
            log.error(f"Failed to send message: {e}")
            raise
        finally:
            await browser.close()
            log.info("Browser closed.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    log.info("=== Family Calendar Bot (Cloud) Starting ===")
    try:
        message = build_message()
        log.info(f"Message to send:\n{message}")
        asyncio.run(send_whatsapp_message(message))
        log.info("=== Bot finished successfully ===")
    except Exception as e:
        log.error(f"=== Bot failed: {e} ===")
        sys.exit(1)


if __name__ == "__main__":
    main()
