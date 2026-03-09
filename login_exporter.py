"""
login_exporter.py  -  LOCAL utility (run on your own machine)

This version saves ONLY the essential WhatsApp session files
(Default/Local Storage, IndexedDB, Cookies) to stay within
GitHub's 64 KB secret size limit.

Steps:
  1. Run this script: python login_exporter.py
  2. A visible Chromium window opens at web.whatsapp.com
  3. Scan the QR code with your phone
  4. Wait until WhatsApp Web is fully loaded (chats visible), then press ENTER
  5. The script zips only the essential session files and prints a Base64 string
  6. Copy that string into GitHub Secret: WHATSAPP_SESSION
"""

import asyncio
import base64
import io
import os
import zipfile

from playwright.async_api import async_playwright

SESSION_DIR = "session_data"

# Only these subdirectories/files are needed to restore a WhatsApp Web session.
# This keeps the ZIP well under GitHub's 64 KB secret limit.
ESSENTIAL_PATHS = [
    os.path.join("Default", "Local Storage"),
    os.path.join("Default", "IndexedDB"),
    os.path.join("Default", "Cookies"),
    os.path.join("Default", "Cookies-journal"),
    os.path.join("Default", "Network", "Cookies"),
]


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
        print("Once WhatsApp is fully loaded (your chats are visible), press ENTER here.")
        input()

        print("[*] Closing browser and saving session...")
        await browser.close()

    # Zip only the essential session files
    zip_buffer = io.BytesIO()
    files_added = 0

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for rel_path in ESSENTIAL_PATHS:
            abs_path = os.path.join(SESSION_DIR, rel_path)
            if os.path.isfile(abs_path):
                zf.write(abs_path, rel_path)
                files_added += 1
                print(f"  [+] Added file: {rel_path}")
            elif os.path.isdir(abs_path):
                for root, dirs, files in os.walk(abs_path):
                    for file in files:
                        filepath = os.path.join(root, file)
                        arcname = os.path.relpath(filepath, SESSION_DIR)
                        zf.write(filepath, arcname)
                        files_added += 1
                        print(f"  [+] Added: {arcname}")

    zip_bytes = zip_buffer.getvalue()
    zip_b64 = base64.b64encode(zip_bytes).decode("utf-8")
    zip_kb = len(zip_bytes) / 1024
    b64_kb = len(zip_b64) / 1024

    print(f"\n[*] Files added: {files_added}")
    print(f"[*] ZIP size:    {zip_kb:.1f} KB")
    print(f"[*] Base64 size: {b64_kb:.1f} KB (GitHub limit: 64 KB)")

    if b64_kb > 64:
        print("\n[WARNING] Base64 is over 64 KB! GitHub will reject it.")
        print("This usually means your IndexedDB is very large.")
        print("Try closing and re-opening WhatsApp Web before running this script,")
        print("or use the --slim flag option below.")
    else:
        print("\n[OK] Size is within GitHub's 64 KB secret limit.")

    # Write to file for easy gh CLI upload
    with open("session_b64.txt", "w") as f:
        f.write(zip_b64)
    print("\n[*] Also saved to session_b64.txt for gh CLI upload.")

    print("\n" + "=" * 60)
    print("Run this command to upload to GitHub:")
    print("  gh secret set WHATSAPP_SESSION < session_b64.txt --repo familieng9/familycalendarbot")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
