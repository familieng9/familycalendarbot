#!/usr/bin/env python3
"""
meal_plan_bot.py — Weekly Japanese Family Meal Plan Bot
Sends a Mon–Fri meal plan + FairPrice grocery list every Saturday via WhatsApp.

Design:
  - Mon–Fri only (no weekend meals)
  - Recipe URL shown below every lunch and dinner
  - Grocery quantities sized for 4 (2 adults + 2 children aged 5 & 7)
  - 4-week rotation (ISO week % 4) — never the same two weeks in a row
  - Hero protein + hero veg reused across multiple meals each week (less waste)
  - Reuses same session_encrypted.zip + launch_persistent_context as family_bot_cloud.py

Required GitHub Secrets:
  SESSION_PASSWORD : Decrypts session_encrypted.zip
"""

import asyncio
import logging
import os
import shutil
import sys
from datetime import date, timedelta

import pytz
import pyzipper
from playwright.async_api import async_playwright

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

SG_TZ             = pytz.timezone("Asia/Singapore")
SESSION_DIR       = "session_data"
SESSION_ZIP       = "session_encrypted.zip"
GROUP_INVITE_CODE = "FHQ7HrFjHEOJQ3fbnl84UC"
DAYS              = ["Mon", "Tue", "Wed", "Thu", "Fri"]

# ─── 4-WEEK ROTATION ─────────────────────────────────────────────────────────
# Serves 4 — 2 adults + 2 children (ages 5 & 7).
# Hero proteins and veg are reused across multiple meals so you buy once.
#
# WEEK 1 — Salmon + Chicken / Cabbage + Cucumber
#   Salmon 500g  → Mon dinner (300g) + Fri lunch ochazuke (200g)
#   Chicken 1kg  → Mon lunch, Wed lunch, Thu lunch + dinner, Fri dinner
#   Cucumber 2pc → Tue sunomono + Tue snack
#   Cabbage ½    → Wed karaage garnish + Fri udon
#   Mushrooms    → Thu nabe only
#
# WEEK 2 — Pork belly + Chicken / Spinach + Daikon
#   Pork belly 250g → Fri chashu ramen only
#   Chicken 500g    → Mon oyakodon + Wed chahan
#   Salmon 300g     → Mon onigiri + Fri chirashi
#   Spinach 1 bag   → Tue hotpot + Thu hambagu salad
#
# WEEK 3 — Beef + Chicken / Bok choy + Broccolini
#   Beef 350g      → Tue gyudon + Thu nikujaga stew
#   Chicken 600g   → Mon soboro + Wed stir-fry + Fri teriyaki + Fri soba
#   Salmon 150g    → Wed onigiri only
#   Bok choy 1-2   → Mon soup + Wed stir-fry
#   Broccolini 2   → Tue side + Fri soba
#
# WEEK 4 — Prawn + Pork mince / Cabbage + Carrot
#   Prawn 350g      → Mon bowl + Tue soba + Thu udon + Fri chirashi
#   Pork mince 500g → Tue gyoza + Wed soboro + Thu curry
#   Cabbage ½ head  → Tue gyoza filling + Fri okonomiyaki
#   Carrot 3pc      → Wed chahan + Thu curry

MEAL_PLANS = {
    1: {
        "Mon": {
            "lunch":      "Teriyaki chicken rice bowl",
            "lunch_url":  "https://www.justonecookbook.com/chicken-teriyaki/",
            "dinner":     "Grilled salmon + miso tofu soup + steamed rice",
            "dinner_url": "https://www.justonecookbook.com/miso-soup/",
            "snack1": "Tuna mayo onigiri",
            "snack2": "Apple slices + cheddar cubes",
        },
        "Tue": {
            "lunch":      "Udon soup with fish cake & soft-boiled egg",
            "lunch_url":  "https://www.justonecookbook.com/udon-noodle-soup/",
            "dinner":     "Pan-fried gyoza + cucumber sunomono + rice",
            "dinner_url": "https://www.justonecookbook.com/gyoza/",
            "snack1": "Cucumber sticks + hummus",
            "snack2": "Banana + rice crackers",
        },
        "Wed": {
            "lunch":      "Chicken karaage rice bowl + shredded cabbage",
            "lunch_url":  "https://www.justonecookbook.com/chicken-karaage/",
            "dinner":     "Cold soba noodles with dashi dipping sauce & egg",
            "dinner_url": "https://www.justonecookbook.com/zaru-soba/",
            "snack1": "Plain salt onigiri",
            "snack2": "Mandarin orange + graham crackers",
        },
        "Thu": {
            "lunch":      "Mild Japanese chicken curry rice",
            "lunch_url":  "https://www.justonecookbook.com/japanese-curry/",
            "dinner":     "Mushroom & tofu nabe hotpot (mild) + rice",
            "dinner_url": "https://www.justonecookbook.com/nabe/",
            "snack1": "Tamagoyaki roll slices",
            "snack2": "Grapes + rice crackers",
        },
        "Fri": {
            "lunch":      "Salmon ochazuke (rice + warm green tea broth)",
            "lunch_url":  "https://www.justonecookbook.com/ochazuke/",
            "dinner":     "Yaki udon with chicken & cabbage",
            "dinner_url": "https://www.justonecookbook.com/yaki-udon/",
            "snack1": "Edamame (lightly salted)",
            "snack2": "Apple + cheddar cheese slices",
        },
    },
    2: {
        "Mon": {
            "lunch":      "Salmon onigiri + cup miso soup",
            "lunch_url":  "https://www.justonecookbook.com/onigiri/",
            "dinner":     "Oyakodon (chicken & egg on rice)",
            "dinner_url": "https://www.justonecookbook.com/oyakodon/",
            "snack1": "Hard-boiled egg + rice crackers",
            "snack2": "Mandarin orange + babybel cheese",
        },
        "Tue": {
            "lunch":      "Zaru soba (cold) with sesame dipping sauce",
            "lunch_url":  "https://www.justonecookbook.com/zaru-soba/",
            "dinner":     "Spinach & tofu miso hotpot + rice",
            "dinner_url": "https://www.justonecookbook.com/nabe/",
            "snack1": "Carrot sticks + cream cheese",
            "snack2": "Banana + graham crackers",
        },
        "Wed": {
            "lunch":      "Chahan (fried rice) with chicken, egg & peas",
            "lunch_url":  "https://www.justonecookbook.com/japanese-fried-rice/",
            "dinner":     "Grilled mackerel + pickled daikon + miso soup + rice",
            "dinner_url": "https://www.justonecookbook.com/grilled-mackerel/",
            "snack1": "Tamagoyaki + cherry tomatoes",
            "snack2": "Apple + rice crackers",
        },
        "Thu": {
            "lunch":      "Udon soup with wakame seaweed & fish cake",
            "lunch_url":  "https://www.justonecookbook.com/udon-noodle-soup/",
            "dinner":     "Hambagu (Japanese hamburger steak) + spinach salad + rice",
            "dinner_url": "https://www.justonecookbook.com/hamburger-steak/",
            "snack1": "Edamame (lightly salted)",
            "snack2": "Grapes + cheddar cubes",
        },
        "Fri": {
            "lunch":      "Chirashi bowl (salmon, tamagoyaki, cucumber, rice)",
            "lunch_url":  "https://www.justonecookbook.com/chirashi-sushi/",
            "dinner":     "Shoyu ramen with chashu pork & soft egg (mild)",
            "dinner_url": "https://www.justonecookbook.com/shoyu-ramen/",
            "snack1": "Tuna mayo onigiri",
            "snack2": "Apple slices + crackers",
        },
    },
    3: {
        "Mon": {
            "lunch":      "Chicken soboro don (minced chicken on rice)",
            "lunch_url":  "https://www.justonecookbook.com/soboro/",
            "dinner":     "Bok choy & tofu clear soup + miso-glazed chicken + rice",
            "dinner_url": "https://www.justonecookbook.com/miso-chicken/",
            "snack1": "Cucumber sticks + hummus",
            "snack2": "Banana + rice crackers",
        },
        "Tue": {
            "lunch":      "Tamagoyaki sandwich (Japanese egg roll in soft bread)",
            "lunch_url":  "https://www.justonecookbook.com/tamagoyaki/",
            "dinner":     "Gyudon (mild beef rice bowl) + steamed broccolini",
            "dinner_url": "https://www.justonecookbook.com/gyudon/",
            "snack1": "Edamame (lightly salted)",
            "snack2": "Apple + babybel cheese",
        },
        "Wed": {
            "lunch":      "Salmon & tuna mayo onigiri duo + miso soup",
            "lunch_url":  "https://www.justonecookbook.com/onigiri/",
            "dinner":     "Chicken & bok choy stir-fry + steamed rice",
            "dinner_url": "https://www.justonecookbook.com/chicken-stir-fry/",
            "snack1": "Plain salt onigiri",
            "snack2": "Mandarin orange + graham crackers",
        },
        "Thu": {
            "lunch":      "Udon with mushrooms, egg & chicken in dashi broth",
            "lunch_url":  "https://www.justonecookbook.com/udon-noodle-soup/",
            "dinner":     "Mild beef nikujaga (potato & beef stew) + rice",
            "dinner_url": "https://www.justonecookbook.com/nikujaga/",
            "snack1": "Tamagoyaki roll slices",
            "snack2": "Grapes + cheddar cubes",
        },
        "Fri": {
            "lunch":      "Chicken teriyaki rice bowl",
            "lunch_url":  "https://www.justonecookbook.com/chicken-teriyaki/",
            "dinner":     "Soba noodles with chicken & broccolini in dashi broth",
            "dinner_url": "https://www.justonecookbook.com/soba-noodles/",
            "snack1": "Rice crackers + cheese",
            "snack2": "Apple slices + crackers",
        },
    },
    4: {
        "Mon": {
            "lunch":      "Prawn & avocado rice bowl with sesame soy dressing",
            "lunch_url":  "https://www.justonecookbook.com/ebi-chili/",
            "dinner":     "Pork mince & silken tofu stir-fry (mild) + rice + miso soup",
            "dinner_url": "https://www.justonecookbook.com/mapo-tofu/",
            "snack1": "Apple slices + cheddar",
            "snack2": "Rice crackers + hummus",
        },
        "Tue": {
            "lunch":      "Cold soba with prawn & cucumber in sesame sauce",
            "lunch_url":  "https://www.justonecookbook.com/zaru-soba/",
            "dinner":     "Pan-fried pork & cabbage gyoza + steamed rice",
            "dinner_url": "https://www.justonecookbook.com/gyoza/",
            "snack1": "Cucumber sticks + cream cheese",
            "snack2": "Banana + graham crackers",
        },
        "Wed": {
            "lunch":      "Chahan (fried rice) with egg, peas & carrot",
            "lunch_url":  "https://www.justonecookbook.com/japanese-fried-rice/",
            "dinner":     "Pork mince soboro don + miso soup",
            "dinner_url": "https://www.justonecookbook.com/soboro/",
            "snack1": "Plain salt onigiri",
            "snack2": "Mandarin orange + cheese",
        },
        "Thu": {
            "lunch":      "Udon soup with prawn, fish cake & carrot",
            "lunch_url":  "https://www.justonecookbook.com/udon-noodle-soup/",
            "dinner":     "Mild pork & potato Japanese curry + rice",
            "dinner_url": "https://www.justonecookbook.com/japanese-curry/",
            "snack1": "Tamagoyaki roll slices",
            "snack2": "Grapes + rice crackers",
        },
        "Fri": {
            "lunch":      "Chirashi bowl (prawn, cucumber, tamagoyaki, sushi rice)",
            "lunch_url":  "https://www.justonecookbook.com/chirashi-sushi/",
            "dinner":     "Cabbage & carrot okonomiyaki (mild) + miso soup",
            "dinner_url": "https://www.justonecookbook.com/okonomiyaki/",
            "snack1": "Edamame (lightly salted)",
            "snack2": "Apple + crackers",
        },
    },
}

# ─── GROCERY LISTS ───────────────────────────────────────────────────────────
# Serves 4 — 2 adults + 2 children (ages 5 & 7). Mon–Fri only.
# Quantities cover all meals that share the same ingredient.

GROCERY_LISTS = {
    1: {
        "🥦 Produce": [
            "Cucumber x2  (Tue sunomono + Tue snack)",
            "Cabbage ½ head  (Wed karaage garnish + Fri udon)",
            "Green onion x1 bunch",
            "Shiitake mushrooms x1 pack  (Thu nabe)",
            "Apples x4", "Mandarin oranges x4", "Grapes x1 bunch", "Bananas x4",
            "Frozen edamame x1 bag",
        ],
        "🥩 Meat / Seafood": [
            "Chicken thigh fillet x1kg  (Mon, Wed, Thu lunch + dinner, Fri)",
            "Salmon fillet x500g  (Mon dinner 300g + Fri ochazuke 200g)",
            "Pork mince x400g  (Tue gyoza)",
            "Fish cake / narutomaki x1 pack  (Tue udon)",
        ],
        "🧊 Chilled": [
            "Eggs x8  (Wed soba, Thu tamagoyaki snack, miso soups)",
            "Silken tofu x1 pack  (Mon miso soup, Thu nabe)",
            "Firm tofu x1 pack  (Thu nabe)",
            "Cheddar / babybel cheese x1 pack",
            "Ready-made gyoza x1 pack  (Tue dinner)",
            "Hummus x2 cups",
        ],
        "🛒 Pantry": [
            "Japanese short-grain rice x2kg",
            "Udon noodles x2 packs", "Soba noodles x1 pack",
            "Miso paste x1 tub",
            "Soy sauce, mirin, sesame oil  (replenish if low)",
            "Dashi stock granules x1 box",
            "Mild Japanese curry roux x1 box",
            "Teriyaki sauce x1 bottle", "Mild tonkatsu sauce x1 bottle",
            "Panko breadcrumbs x1 bag",
            "Nori sheets x1 pack",
            "Green tea bags (ochazuke) x1 box",
            "Rice crackers x2 packs", "Graham crackers x1 box",
            "Sushi rice vinegar", "Canned tuna x2",
        ],
    },
    2: {
        "🥦 Produce": [
            "Spinach x1 bag  (Tue hotpot + Thu hambagu salad)",
            "Daikon x1 small  (Wed pickled side)",
            "Carrot x2", "Cucumber x2",
            "Cherry tomatoes x1 punnet  (Wed snack)",
            "Green onion x1 bunch",
            "Frozen peas x1 bag  (Wed chahan)",
            "Frozen edamame x1 bag  (Thu snack)",
            "Apples x4", "Mandarin oranges x4", "Grapes x1 bunch", "Bananas x4",
        ],
        "🥩 Meat / Seafood": [
            "Chicken thigh fillet x500g  (Mon oyakodon + Wed chahan)",
            "Salmon fillet x300g  (Mon onigiri + Fri chirashi)",
            "Pork belly x250g  (Fri chashu ramen)",
            "Minced pork + beef mix x300g  (Thu hambagu)",
            "Mackerel fillets x2  (Wed dinner)",
            "Fish cake x1 pack  (Thu udon)",
        ],
        "🧊 Chilled": [
            "Eggs x8  (Mon oyakodon, Wed tamagoyaki, Fri chirashi)",
            "Silken tofu x1 pack  (Tue hotpot + miso soups)",
            "Firm tofu x1 pack  (Tue hotpot)",
            "Cream cheese x1 small tub  (Tue snack)",
            "Babybel / cheddar cheese x1 pack",
            "Wakame seaweed x1 pack  (Thu udon)",
        ],
        "🛒 Pantry": [
            "Japanese short-grain rice x2kg",
            "Udon noodles x2 packs", "Soba noodles x1 pack",
            "Instant ramen (mild) x2 packs  (Fri base)",
            "Miso paste x1 tub",
            "Soy sauce, mirin, sesame oil  (replenish if low)",
            "Dashi stock granules",
            "Kewpie sesame dressing x1 bottle",
            "Nori sheets x1 pack",
            "Rice crackers x2 packs", "Graham crackers x1 box",
            "Sushi rice vinegar", "Canned tuna x1",
        ],
    },
    3: {
        "🥦 Produce": [
            "Bok choy x2 bunches  (Mon soup + Wed stir-fry)",
            "Broccolini x2 bunches  (Tue gyudon side + Fri soba)",
            "Cucumber x2  (Wed onigiri side + snacks)",
            "Potato x3  (Thu nikujaga)",
            "Green onion x1 bunch",
            "Shiitake x1 pack  (Thu udon + Thu stew)",
            "Frozen edamame x1 bag  (Tue snack)",
            "Apples x4", "Mandarin oranges x4", "Grapes x1 bunch", "Bananas x4",
        ],
        "🥩 Meat / Seafood": [
            "Chicken thigh fillet x600g  (Mon soboro + Wed stir-fry + Fri teriyaki + Fri soba)",
            "Beef thinly sliced x350g  (Tue gyudon + Thu nikujaga)",
            "Salmon fillet x150g  (Wed onigiri)",
        ],
        "🧊 Chilled": [
            "Eggs x8  (Mon soboro topping, Thu tamagoyaki, Thu udon)",
            "Silken tofu x1 pack  (Mon soup + miso soups)",
            "Firm tofu x1 pack  (Mon soup)",
            "Babybel / cheddar cheese x1 pack",
            "Hummus x2 cups  (Mon snack)",
            "Cream cheese x1 small tub",
            "Soft sandwich bread x4 slices  (Tue tamagoyaki sandwich)",
        ],
        "🛒 Pantry": [
            "Japanese short-grain rice x2kg",
            "Udon noodles x2 packs", "Soba noodles x1 pack",
            "Miso paste x1 tub",
            "Soy sauce, mirin, sesame oil  (replenish if low)",
            "Dashi stock granules",
            "Teriyaki sauce x1 bottle",
            "Mild Japanese curry roux x1 box",
            "Nori sheets x1 pack",
            "Rice crackers x2 packs", "Graham crackers x1 box",
            "Canned tuna x2", "Sushi rice vinegar",
            "Ketchup (omurice sauce)",
        ],
    },
    4: {
        "🥦 Produce": [
            "Cabbage ½ head  (Tue gyoza filling + Fri okonomiyaki)",
            "Carrot x3  (Wed chahan + Thu curry)",
            "Cucumber x2  (Tue soba + Fri chirashi)",
            "Avocado x1  (Mon bowl)",
            "Potato x3  (Thu pork curry)",
            "Green onion x1 bunch",
            "Frozen peas x1 bag  (Wed chahan)",
            "Frozen edamame x1 bag  (Fri snack)",
            "Apples x4", "Mandarin oranges x4", "Grapes x1 bunch", "Bananas x4",
        ],
        "🥩 Meat / Seafood": [
            "Prawns x350g peeled  (Mon bowl, Tue soba, Thu udon, Fri chirashi)",
            "Pork mince x500g  (Tue gyoza, Wed soboro, Thu curry)",
            "Fish cake x1 pack  (Thu udon)",
        ],
        "🧊 Chilled": [
            "Eggs x8  (Wed chahan, Thu tamagoyaki, Fri chirashi)",
            "Silken tofu x1 pack  (Mon stir-fry + miso soups)",
            "Cheddar / babybel cheese x1 pack",
            "Cream cheese x1 small tub  (Tue snack)",
            "Hummus x2 cups  (Mon snack)",
        ],
        "🛒 Pantry": [
            "Japanese short-grain rice x2kg",
            "Udon noodles x2 packs", "Soba noodles x1 pack",
            "Miso paste x1 tub",
            "Soy sauce, mirin, sesame oil  (replenish if low)",
            "Dashi stock granules",
            "Mild Japanese curry roux x1 box",
            "Mild tonkatsu sauce x1 bottle",
            "Okonomiyaki flour x1 bag (or plain flour)",
            "Panko breadcrumbs x1 bag",
            "Nori sheets x1 pack",
            "Rice crackers x2 packs", "Graham crackers x1 box",
            "Sushi rice vinegar",
            "Kewpie mayo x1 (mild okonomiyaki topping)",
            "Canned tuna x1",
        ],
    },
}

# ─── FORMATTERS ──────────────────────────────────────────────────────────────

DAY_EMOJIS = ["🌱", "🌿", "🍃", "🌾", "🎋"]


def get_week_slot(for_date: date) -> int:
    slot = for_date.isocalendar()[1] % 4
    return slot if slot != 0 else 4


def format_meal_plan(slot: int, week_start: date) -> str:
    plan     = MEAL_PLANS[slot]
    week_end = week_start + timedelta(days=4)  # Friday
    lines = [
        f"🍱 *FAMILY MEAL PLAN — Rotation {slot}/4*",
        f"📆 {week_start.strftime('%d %b')} – {week_end.strftime('%d %b %Y')} · serves 4",
        "",
    ]
    for i, day in enumerate(DAYS):
        day_date = week_start + timedelta(days=i)
        d = plan[day]
        lines.append(f"{DAY_EMOJIS[i]} *{day} {day_date.strftime('%d %b')}*")
        lines.append(f"  🥗 Lunch: {d['lunch']}")
        lines.append(f"  🔗 {d['lunch_url']}")
        lines.append(f"  🍽 Dinner: {d['dinner']}")
        lines.append(f"  🔗 {d['dinner_url']}")
        lines.append(f"  🏫 Recess: {d['snack1']}")
        lines.append(f"  🎒 Break: {d['snack2']}")
        lines.append("")
    return "\n".join(lines)


def format_grocery_list(slot: int, week_start: date) -> str:
    grocery = GROCERY_LISTS[slot]
    lines = [
        "🛒 *GROCERY LIST — FairPrice*",
        f"Week of {week_start.strftime('%d %b')} · serves 4 · qty covers shared meals",
        "",
    ]
    for section, items in grocery.items():
        lines.append(f"*{section}*")
        for item in items:
            lines.append(f"  • {item}")
        lines.append("")
    lines.append("✅ _Happy shopping! Tick off as you go_ 🧺")
    return "\n".join(lines)


# ─── SESSION RESTORE ─────────────────────────────────────────────────────────

def restore_whatsapp_session() -> None:
    password = os.environ.get("SESSION_PASSWORD")
    if not password:
        raise EnvironmentError("SESSION_PASSWORD is not set.")
    if not os.path.exists(SESSION_ZIP):
        raise FileNotFoundError(f"{SESSION_ZIP} not found.")

    if os.path.exists(SESSION_DIR):
        shutil.rmtree(SESSION_DIR)
    os.makedirs(SESSION_DIR, exist_ok=True)

    log.info(f"Decrypting {SESSION_ZIP} -> {SESSION_DIR}/")
    with pyzipper.AESZipFile(SESSION_ZIP, "r") as zf:
        zf.setpassword(password.encode())
        for info in zf.infolist():
            normalised = info.filename.replace("\\", "/")
            dest = os.path.join(SESSION_DIR, normalised)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            if not normalised.endswith("/"):
                with zf.open(info) as src, open(dest, "wb") as dst:
                    dst.write(src.read())
    log.info("Session restored.")


# ─── WHATSAPP SENDER ─────────────────────────────────────────────────────────

async def send_whatsapp(messages: list) -> None:
    restore_whatsapp_session()
    group_url = f"https://web.whatsapp.com/accept?code={GROUP_INVITE_CODE}"

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
                "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            ],
        )

        page = browser.pages[0] if browser.pages else await browser.new_page()
        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins',   { get: () => [1,2,3,4,5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US','en'] });
            window.chrome = { runtime: {} };
        """)

        try:
            log.info("Loading WhatsApp Web...")
            await page.goto("https://web.whatsapp.com", timeout=60000, wait_until="domcontentloaded")

            log.info("Checking for expired session...")
            try:
                await page.wait_for_selector('[data-testid="link-device-qr-code"]', timeout=8000)
                await page.screenshot(path="debug_01_qr_screen.png", full_page=True)
                raise RuntimeError(
                    "SESSION EXPIRED — Run login_exporter.py locally, scan the QR, "
                    "then commit the new session_encrypted.zip."
                )
            except RuntimeError:
                raise
            except Exception:
                log.info("Session looks valid.")

            log.info("Waiting for chat list (up to 90s)...")
            try:
                await page.wait_for_selector(
                    '[data-testid="chat-list"], [aria-label="Chat list"], ._aigw, #pane-side',
                    timeout=90000,
                )
                log.info("Chat list loaded.")
            except Exception:
                await page.screenshot(path="debug_01_no_chatlist.png", full_page=True)
                raise RuntimeError("WhatsApp session invalid — re-run login_exporter.py.")

            await asyncio.sleep(5)

            log.info("Navigating to group...")
            await page.goto(group_url, timeout=30000, wait_until="domcontentloaded")
            await asyncio.sleep(5)
            await page.screenshot(path="debug_02_group.png", full_page=True)

            compose_selector = (
                'div[contenteditable="true"][data-tab="10"], '
                'footer div[contenteditable="true"], '
                '[data-testid="conversation-compose-box-input"]'
            )
            try:
                await page.wait_for_selector(compose_selector, timeout=15000)
                compose = page.locator(compose_selector).first
                log.info("Compose box ready.")
            except Exception:
                await page.screenshot(path="debug_03_no_compose.png", full_page=True)
                raise RuntimeError("Could not reach compose box.")

            for idx, msg in enumerate(messages):
                await compose.click()
                await asyncio.sleep(0.5)
                lines = msg.split("\n")
                for i, line in enumerate(lines):
                    await compose.type(line, delay=15)
                    if i < len(lines) - 1:
                        await page.keyboard.press("Shift+Enter")
                await page.keyboard.press("Enter")
                log.info(f"Sent message {idx + 1}/{len(messages)}")
                await asyncio.sleep(4)

            await page.screenshot(path="debug_04_sent.png")

        except Exception as exc:
            log.error(f"Failed: {exc}")
            raise
        finally:
            await browser.close()
            log.info("Browser closed.")


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main() -> None:
    log.info("=== Meal Plan Bot Starting ===")

    today = date.today()
    if today.weekday() == 5:          # Saturday → plan for next week (Mon)
        week_start = today + timedelta(days=2)
    else:
        week_start = today - timedelta(days=today.weekday())

    iso_week = week_start.isocalendar()[1]
    slot     = get_week_slot(week_start)
    log.info(f"Today: {today} | Week start: {week_start} | ISO week: {iso_week} | Slot: {slot}/4")

    meal_msg    = format_meal_plan(slot, week_start)
    grocery_msg = format_grocery_list(slot, week_start)

    log.info("\n─── MEAL PLAN ───\n" + meal_msg)
    log.info("\n─── GROCERY LIST ───\n" + grocery_msg)

    if os.environ.get("SESSION_PASSWORD"):
        asyncio.run(send_whatsapp([meal_msg, grocery_msg]))
        log.info("=== Bot finished successfully ===")
    else:
        log.info("SESSION_PASSWORD not set — dry run only.")


if __name__ == "__main__":
    main()
