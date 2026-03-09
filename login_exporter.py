"""
login_exporter.py  -  LOCAL utility (run on your own machine)

Steps:
  1. Run this script: python login_exporter.py
  2. A visible Chromium window opens at web.whatsapp.com
  3. Scan the QR code with your phone
  4. Wait until WhatsApp Web is fully loaded, then press ENTER in the terminal
  5. The script zips session_data/ and prints a Base64 string
  6. Copy that string into GitHub Secret: WHATSAPP_SESSION
"""

import asyncio
import base64
import os
import zipfile
import io

from playwright.async_api import async_playwright

SESSION_DIR = "session_data"


async def main():
    print("[*] Launching visible browser to capture WhatsApp session...")
    async with async_playwright() as p:
        browser = await p.chromium.launch_persistent_context(
            user_data_dir=SESSION_DIR,
            headless=False,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        page = browser.pages[0] if browser.pages else await browser.new_page()

        print("[*] Navigating to WhatsApp Web...")
        await page.goto("https://web.whatsapp.com", timeout=60000)

        print("\n[ACTION REQUIRED] Scan the QR code in the browser window.")
        print("Once WhatsApp is fully loaded (your chats are visible), come back here and press ENTER.")
        input()

        print("[*] Closing browser and saving session...")
        await browser.close()

    # Zip the session directory into memory
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(SESSION_DIR):
            for file in files:
                filepath = os.path.join(root, file)
                arcname = os.path.relpath(filepath, SESSION_DIR)
                zf.write(filepath, arcname)

    zip_b64 = base64.b64encode(zip_buffer.getvalue()).decode("utf-8")

    print("\n" + "=" * 60)
    print("SUCCESS! Copy the string below into GitHub Secret: WHATSAPP_SESSION")
    print("=" * 60)
    print(zip_b64)
    print("=" * 60)
    print(f"\nSession data folder: {SESSION_DIR}/")
    print("Keep this folder locally as a backup.")


if __name__ == "__main__":
    asyncio.run(main())
