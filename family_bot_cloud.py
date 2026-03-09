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
        raise FileNotFoundError(
            f"{SESSION_ZIP} not found. Commit it to the repo after running login_exporter.py."
        )

    # Clean up any previous extraction
    if os.path.exists(SESSION_DIR):
        shutil.rmtree(SESSION_DIR)
    os.makedirs(SESSION_DIR, exist_ok=True)

    log.info(f"Decrypting {SESSION_ZIP} -> {SESSION_DIR}/")
    with pyzipper.AESZipFile(SESSION_ZIP, "r") as zf:
        zf.setpassword(password.encode())
        # Extract manually to fix Windows backslash paths on Linux
        for zip_info in zf.infolist():
            # Normalise path separators (Windows -> Linux)
            normalised = zip_info.filename.replace("\\", "/").replace("\\\\", "/")
            dest = os.path.join(SESSION_DIR, normalised)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            if not normalised.endswith("/"):
                with zf.open(zip_info) as src, open(dest, "wb") as dst:
                    dst.write(src.read())
                log.info(f"  Extracted: {normalised}")

    log.info("Session restored successfully.")

    # Log what was actually extracted for debugging
    for root, dirs, files in os.walk(SESSION_DIR):
        for f in files:
            p = os.path.join(root, f)
            log.info(f"  File: {os.path.relpath(p, SESSION_DIR)}  ({os.path.getsize(p)} bytes)")


# ---------------------------------------------------------------------------
# WhatsApp sending
# ---------------------------------------------------------------------------

async def send_whatsapp_message(message: str):
    restore_whatsapp_session()

    async with async_playwright() as p:
        log.info("Launching headless Chromium with persistent context...")
        browser = await p.chromium.launch_persistent_context(
            user_data_dir=SESSION_DIR,
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--disable-extensions",
                "--disable-gpu",
                "--window-size=1280,800",
                "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            ],
        )

        page = browser.pages[0] if browser.pages else await browser.new_page()

        # Override navigator properties to avoid headless detection
        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
            window.chrome = { runtime: {} };
        """)

        group_url = f"https://web.whatsapp.com/accept?code={GROUP_INVITE_CODE}"

        try:
            log.info("Loading WhatsApp Web...")
            await page.goto("https://web.whatsapp.com", timeout=60000, wait_until="domcontentloaded")

            log.info("Waiting up to 60s for chat list (session check)...")
            try:
                # Try multiple selectors WhatsApp uses for the main UI
                await page.wait_for_selector(
                    '[data-testid="chat-list"], [data-testid="default-user"], #app .two',
                    timeout=60000
                )
                log.info("Session valid. WhatsApp Web loaded.")
            except Exception:
                await page.screenshot(path="debug_screenshot.png")
                page_title = await page.title()
                log.error(f"Page title at timeout: {page_title}")
                raise RuntimeError(
                    "WhatsApp session invalid or expired. "
                    "Re-run login_exporter.py locally, commit the new session_encrypted.zip."
                )

            log.info(f"Navigating to group: {group_url}")
            await page.goto(group_url, timeout=30000, wait_until="domcontentloaded")
            await asyncio.sleep(3)

            log.info("Waiting for compose box...")
            await page.wait_for_selector(
                '[data-testid="conversation-compose-box-input"]', timeout=30000
            )

            input_box = page.locator('[data-testid="conversation-compose-box-input"]')
            await input_box.click()
            await asyncio.sleep(1)

            lines = message.split("\n")
            for i, line in enumerate(lines):
                await input_box.type(line, delay=30)
                if i < len(lines) - 1:
                    await page.keyboard.press("Shift+Enter")

            await page.keyboard.press("Enter")
            log.info("Message sent!")
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
