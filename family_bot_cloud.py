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
# Diagnostics helper
# ---------------------------------------------------------------------------

async def dump_page_state(page, label: str):
    path = f"debug_{label}.png"
    await page.screenshot(path=path, full_page=True)
    log.info(f"Screenshot saved: {path}")
    try:
        testids = await page.evaluate("""
            () => [...document.querySelectorAll('[data-testid]')]
                .map(el => el.getAttribute('data-testid'))
                .filter((v,i,a) => a.indexOf(v) === i)
                .slice(0, 50)
        """)
        log.info(f"data-testid values on page: {testids}")
    except Exception as e:
        log.info(f"Could not enumerate testids: {e}")


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

            # Wait for the full WhatsApp UI including the sidebar chat list
            log.info("Waiting for WhatsApp sidebar (up to 90s)...")
            try:
                await page.wait_for_selector('[data-testid="chat-list"]', timeout=90000)
                log.info("Chat list loaded.")
            except Exception:
                await dump_page_state(page, "01_no_chatlist")
                raise RuntimeError("WhatsApp session invalid. Re-run login_exporter.py and recommit session_encrypted.zip.")

            # Give the sidebar extra time to fully populate with chats
            log.info("Waiting 8s for sidebar chats to populate...")
            await asyncio.sleep(8)
            await dump_page_state(page, "02_after_load")

            # --- Navigate to group via invite URL ---
            group_url = f"https://web.whatsapp.com/accept?code={GROUP_INVITE_CODE}"
            log.info(f"Navigating to: {group_url}")
            await page.goto(group_url, timeout=30000, wait_until="domcontentloaded")
            await asyncio.sleep(5)
            await dump_page_state(page, "03_after_invite")

            # Check if compose box appeared directly
            compose = page.locator('[data-testid="conversation-compose-box-input"]')
            compose_visible = await compose.is_visible(timeout=2000)

            if not compose_visible:
                log.info("Compose box not visible. Looking for any clickable action...")

                # Log all button texts
                btns = page.locator("button")
                n = await btns.count()
                log.info(f"Found {n} buttons on page:")
                for i in range(min(n, 20)):
                    txt = (await btns.nth(i).inner_text()).strip()
                    log.info(f"  Button [{i}]: '{txt}'")

                # Try clicking a join/continue/open button
                for btn_text in ["Continue to WhatsApp", "Continue", "Open", "Join Group", "Join", "OK"]:
                    try:
                        btn = page.get_by_role("button", name=btn_text, exact=False)
                        if await btn.is_visible(timeout=1500):
                            log.info(f"Clicking button: '{btn_text}'")
                            await btn.click()
                            await asyncio.sleep(4)
                            break
                    except Exception:
                        pass

                await dump_page_state(page, "04_after_button_click")

                # Last resort: go back to main page and use keyboard shortcut Ctrl+K (new chat search)
                compose_visible = await compose.is_visible(timeout=3000)
                if not compose_visible:
                    log.info("Still no compose. Going back to main page and using Ctrl+K search...")
                    await page.goto("https://web.whatsapp.com", timeout=30000, wait_until="domcontentloaded")
                    await asyncio.sleep(5)
                    # Ctrl+K or Ctrl+/ opens the search box in WhatsApp Web
                    await page.keyboard.press("Control+k")
                    await asyncio.sleep(1)
                    await page.keyboard.type("Family", delay=80)
                    await asyncio.sleep(3)
                    await dump_page_state(page, "05_after_ctrlk_search")
                    # Press enter on first result
                    await page.keyboard.press("Enter")
                    await asyncio.sleep(2)

            # Final compose box check
            try:
                await compose.wait_for(timeout=15000)
                log.info("Compose box ready.")
            except Exception:
                await dump_page_state(page, "06_no_compose_final")
                raise RuntimeError("Could not reach compose box. Check debug screenshots in artifacts.")

            # Type and send
            await compose.click()
            await asyncio.sleep(0.5)
            lines = message.split("\n")
            for i, line in enumerate(lines):
                await compose.type(line, delay=20)
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
