"""
family_bot_cloud.py  -  Cloud automation script for GitHub Actions

Requires GitHub Secrets:
  - WHATSAPP_SESSION   : Base64-encoded ZIP of Playwright session_data/
  - GOOGLE_CREDENTIALS : Content of credentials.json (Google OAuth client)
  - GOOGLE_TOKEN       : Content of token.json (Google OAuth user token)
"""

import asyncio
import base64
import datetime
import io
import json
import logging
import os
import sys
import zipfile

import pytz
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from playwright.async_api import async_playwright

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

GROUP_INVITE_CODE = "FHQ7HrFjHEOJQ3fbnl84UC"   # From your invite link

CALENDAR_IDS = [
    "primary",
    "0gs624o1448ja48f0ielplj9co@group.calendar.google.com",  # Tessa x Popo
    "family07313615549286623759@group.calendar.google.com",  # Family
]

SESSION_DIR = "session_data"
SG_TZ = pytz.timezone("Asia/Singapore")

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
# Google Calendar helpers
# ---------------------------------------------------------------------------

def restore_google_credentials() -> Credentials:
    """Build Credentials from environment variables (no files on disk)."""
    credentials_json = os.environ.get("GOOGLE_CREDENTIALS")
    token_json = os.environ.get("GOOGLE_TOKEN")

    if not credentials_json or not token_json:
        raise EnvironmentError(
            "GOOGLE_CREDENTIALS and GOOGLE_TOKEN environment variables must be set."
        )

    # Write temp files that the Google library can read
    with open("credentials.json", "w") as f:
        f.write(credentials_json)
    with open("token.json", "w") as f:
        f.write(token_json)

    creds = Credentials.from_authorized_user_file("token.json")
    return creds


def get_tomorrow_events() -> list[str]:
    """Fetch all events for tomorrow (Singapore time) from all calendars."""
    creds = restore_google_credentials()
    service = build("calendar", "v3", credentials=creds)

    now_sg = datetime.datetime.now(SG_TZ)
    tomorrow_date = (now_sg + datetime.timedelta(days=1)).date()

    start_of_tomorrow = SG_TZ.localize(
        datetime.datetime.combine(tomorrow_date, datetime.time.min)
    ).isoformat()
    end_of_tomorrow = SG_TZ.localize(
        datetime.datetime.combine(tomorrow_date, datetime.time.max)
    ).isoformat()

    log.info(f"Fetching events for {tomorrow_date} (SGT)")

    all_events = []
    for cal_id in CALENDAR_IDS:
        try:
            result = (
                service.events()
                .list(
                    calendarId=cal_id,
                    timeMin=start_of_tomorrow,
                    timeMax=end_of_tomorrow,
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
            )
            for event in result.get("items", []):
                start = event["start"].get("dateTime", event["start"].get("date"))
                if "T" in start:
                    event_dt = datetime.datetime.fromisoformat(start).astimezone(SG_TZ)
                    time_str = event_dt.strftime("%I:%M %p")
                else:
                    time_str = "All Day"
                all_events.append(f"• {time_str}: {event['summary']}")
        except Exception as e:
            log.error(f"Error fetching calendar {cal_id}: {e}")

    return all_events


def build_message() -> str:
    """Build the WhatsApp message string."""
    events = get_tomorrow_events()
    if not events:
        return "🌙 No activities scheduled for tomorrow!"
    return "📅 Tomorrow's Schedule:\n" + "\n".join(events)


# ---------------------------------------------------------------------------
# WhatsApp session helpers
# ---------------------------------------------------------------------------

def restore_whatsapp_session():
    """Unzip the Base64-encoded session from WHATSAPP_SESSION env var."""
    session_b64 = os.environ.get("WHATSAPP_SESSION")
    if not session_b64:
        raise EnvironmentError("WHATSAPP_SESSION environment variable is not set.")

    log.info("Restoring WhatsApp session from environment variable...")
    zip_data = base64.b64decode(session_b64)
    zip_buffer = io.BytesIO(zip_data)

    with zipfile.ZipFile(zip_buffer, "r") as zf:
        zf.extractall(SESSION_DIR)

    log.info(f"Session restored to ./{SESSION_DIR}/")


# ---------------------------------------------------------------------------
# WhatsApp sending logic
# ---------------------------------------------------------------------------

async def send_whatsapp_message(message: str):
    """Open WhatsApp Web headlessly and send a message to the group."""
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
            ],
        )

        page = browser.pages[0] if browser.pages else await browser.new_page()

        # Navigate directly to the group using the invite link approach
        group_url = f"https://web.whatsapp.com/accept?code={GROUP_INVITE_CODE}"
        log.info(f"Navigating to WhatsApp group via: {group_url}")

        try:
            await page.goto("https://web.whatsapp.com", timeout=60000)
            log.info("Waiting for WhatsApp Web to load...")

            # Wait for the chat list to confirm session is valid
            try:
                await page.wait_for_selector('[data-testid="chat-list"]', timeout=45000)
                log.info("Session is valid. WhatsApp Web loaded successfully.")
            except Exception:
                log.error("Session may be expired. Could not find chat list.")
                await page.screenshot(path="debug_screenshot.png")
                raise RuntimeError("WhatsApp session invalid or expired. Re-run login_exporter.py locally.")

            # Navigate to the group
            await page.goto(group_url, timeout=30000)
            log.info("Navigated to group invite URL.")

            # Wait for the message input box
            await page.wait_for_selector('[data-testid="conversation-compose-box-input"]', timeout=30000)
            log.info("Message input box found.")

            input_box = page.locator('[data-testid="conversation-compose-box-input"]')
            await input_box.click()

            # Type the message (split lines to handle newlines properly)
            for line in message.split("\n"):
                await input_box.type(line)
                await page.keyboard.press("Shift+Enter")

            # Remove the trailing extra newline by pressing Backspace once
            await page.keyboard.press("Backspace")

            # Send the message
            await page.keyboard.press("Enter")
            log.info("Message sent successfully!")

            # Brief pause to allow the message to be delivered
            await asyncio.sleep(3)

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
