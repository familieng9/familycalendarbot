"""
login_exporter.py  -  LOCAL utility (run on your own machine)

Because the WhatsApp session (IndexedDB) is several MB, it cannot fit
in a GitHub Secret (64 KB limit). Instead, this script:

  1. Launches Chromium so you can scan the QR code
  2. Zips the essential session files
  3. Encrypts the ZIP with a password you choose (using pyzipper AES-256)
  4. Saves the encrypted file as  session_encrypted.zip
  5. You commit that file to the repo  (it is safe - it is encrypted)
  6. Only the short password goes into GitHub Secrets as SESSION_PASSWORD

Setup (one-time):
  pip install playwright pyzipper
  playwright install chromium

Usage:
  python login_exporter.py
"""

import asyncio
import getpass
import os
import shutil

import pyzipper
from playwright.async_api import async_playwright

SESSION_DIR = "session_data"
OUTPUT_FILE = "session_encrypted.zip"

ESSENTIAL_PATHS = [
    os.path.join("Default", "Local Storage"),
    os.path.join("Default", "IndexedDB"),
    os.path.join("Default", "Network", "Cookies"),
    os.path.join("Default", "Cookies"),
]


async def capture_session():
    if os.path.exists(SESSION_DIR):
        shutil.rmtree(SESSION_DIR)

    print("[*] Launching visible Chromium browser...")
    async with async_playwright() as p:
        browser = await p.chromium.launch_persistent_context(
            user_data_dir=SESSION_DIR,
            headless=False,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        page = browser.pages[0] if browser.pages else await browser.new_page()
        await page.goto("https://web.whatsapp.com", timeout=60000)

        print("\n[ACTION REQUIRED] Scan the QR code in the browser window.")
        print("Once your chats are visible, come back here and press ENTER.")
        input()

        print("[*] Closing browser...")
        await browser.close()


def encrypt_session(password: str):
    files_added = 0
    total_bytes = 0

    print(f"[*] Creating encrypted ZIP: {OUTPUT_FILE}")
    with pyzipper.AESZipFile(
        OUTPUT_FILE, "w",
        compression=pyzipper.ZIP_DEFLATED,
        encryption=pyzipper.WZ_AES
    ) as zf:
        zf.setpassword(password.encode())
        for rel_path in ESSENTIAL_PATHS:
            abs_path = os.path.join(SESSION_DIR, rel_path)
            if os.path.isfile(abs_path):
                zf.write(abs_path, rel_path)
                files_added += 1
            elif os.path.isdir(abs_path):
                for root, dirs, files in os.walk(abs_path):
                    for file in files:
                        filepath = os.path.join(root, file)
                        arcname = os.path.relpath(filepath, SESSION_DIR)
                        size = os.path.getsize(filepath)
                        zf.write(filepath, arcname)
                        files_added += 1
                        total_bytes += size
                        print(f"  [+] {arcname}  ({size/1024:.0f} KB)")

    final_size = os.path.getsize(OUTPUT_FILE)
    print(f"\n[*] Done: {files_added} files, {total_bytes/1024:.0f} KB raw -> {final_size/1024:.0f} KB encrypted")


def main():
    print("=" * 60)
    print("  WhatsApp Session Exporter (encrypted repo file method)")
    print("=" * 60)

    asyncio.run(capture_session())

    print("\n[*] Choose a password to encrypt the session.")
    print("    You will store this password as GitHub Secret: SESSION_PASSWORD")
    password = getpass.getpass("    Enter password: ")
    confirm  = getpass.getpass("    Confirm password: ")
    if password != confirm:
        print("[ERROR] Passwords do not match. Exiting.")
        return

    encrypt_session(password)

    print("\n" + "=" * 60)
    print("NEXT STEPS:")
    print(f"  1. Commit {OUTPUT_FILE} to your repo:")
    print(f"       git add {OUTPUT_FILE}")
    print(f"       git commit -m \"Add encrypted WhatsApp session\"")
    print(f"       git push")
    print(f"  2. Add your password to GitHub Secrets as:  SESSION_PASSWORD")
    print(f"       gh secret set SESSION_PASSWORD --repo familieng9/familycalendarbot")
    print(f"     (it will prompt you to type the password)")
    print("=" * 60)


if __name__ == "__main__":
    main()
