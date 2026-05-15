#!/usr/bin/env python3
"""
meal_plan_bot.py — Weekly Japanese Family Meal Plan Bot
Sends a 7-day meal plan + FairPrice grocery list every Saturday via WhatsApp.

Design:
  - 4-week rotation (ISO week % 4) — never the same two weeks in a row
  - Each week has a "hero protein" + "hero veg" shared across 2-3 meals (less waste)
  - Recipe of the Week scraped from Just One Cookbook via urllib (no AI API needed)
  - Reuses the same session_encrypted.zip + launch_persistent_context pattern
    as family_bot_cloud.py (Chromium user-data-dir, not storage_state JSON)

Required GitHub Secrets:
  SESSION_PASSWORD : Decrypts session_encrypted.zip (same secret as family_bot_cloud.py)
"""

import asyncio
import datetime
import logging
import os
import re
import shutil
import sys
import urllib.error
import urllib.request
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

SG_TZ            = pytz.timezone("Asia/Singapore")
SESSION_DIR      = "session_data"
SESSION_ZIP      = "session_encrypted.zip"
GROUP_INVITE_CODE = "FHQ7HrFjHEOJQ3fbnl84UC"   # same group as family_bot_cloud.py
DAYS             = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# ─── 4-WEEK ROTATION ─────────────────────────────────────────────────────────
#
# Each week clusters ingredients to reduce waste.  Quantities in the grocery
# list already cover every meal that shares the same item.
#
# WEEK 1 — Salmon + Chicken / Cabbage + Cucumber + Mushrooms
#   Salmon 400g  → Mon dinner + Fri lunch
#   Chicken 800g → Mon lunch, Wed lunch, Thu curry, Fri dinner, Sun katsu
#   Cabbage 1/2  → Wed garnish + Fri udon
#   Cucumber 3pc → Tue sunomono + Tue snack + Sat maki
#   Mushrooms x2 → Thu nabe + Sat sukiyaki
#   Tofu x2      → Mon miso + Thu nabe + Sat sukiyaki
#
# WEEK 2 — Pork belly + Chicken / Spinach + Mushrooms
#   Pork belly 500g → Fri chashu ramen + Sat shabu-shabu
#   Chicken 400g    → Mon oyakodon + Wed chahan + Sun curry udon
#   Salmon 300g     → Mon onigiri + Fri chirashi
#   Spinach x2      → Tue hotpot + Thu salad + Sat shabu-shabu
#   Mushrooms x2    → Tue hotpot + Sat shabu + Sun zosui
#
# WEEK 3 — Beef + Chicken / Bok choy + Broccolini + Mushrooms
#   Beef 500g      → Tue gyudon + Sat sukiyaki + Sun curry rice
#   Chicken 600g   → Mon soboro + Wed stir-fry + Fri teriyaki + Fri soba
#   Salmon 300g    → Wed onigiri + Sat handrolls
#   Bok choy x2   → Mon soup + Wed stir-fry + Sat sukiyaki
#   Broccolini x2 → Tue side + Fri soba
#   Mushrooms x2  → Thu udon + Thu stew + Sat sukiyaki
#
# WEEK 4 — Prawn + Pork mince / Cabbage + Carrot + Cucumber
#   Prawn 500g       → Mon bowl, Tue soba, Thu udon, Fri chirashi, Sat handrolls
#   Pork mince 500g  → Tue gyoza + Wed soboro + Thu curry
#   Pork belly 400g  → Sat shabu-shabu + Sun katsu
#   Cabbage 1 head   → Tue gyoza + Fri okonomiyaki + Sat shabu
#   Carrot 4pc       → Wed chahan + Thu curry + Sat shabu
#   Cucumber 3pc     → Tue soba + Fri chirashi + Sat handrolls

MEAL_PLANS = {
    1: {
        "Mon": {
            "lunch":  "Teriyaki chicken rice bowl",
            "dinner": "Grilled salmon + miso tofu soup + steamed rice",
            "snack1": "Tuna mayo onigiri",
            "snack2": "Apple slices + cheddar cubes",
        },
        "Tue": {
            "lunch":  "Udon soup with fish cake & soft-boiled egg",
            "dinner": "Pan-fried gyoza + cucumber sunomono + rice",
            "snack1": "Cucumber sticks + hummus",
            "snack2": "Banana + rice crackers",
        },
        "Wed": {
            "lunch":  "Chicken karaage rice bowl + shredded cabbage",
            "dinner": "Cold soba noodles with dashi dipping sauce & egg",
            "snack1": "Plain salt onigiri",
            "snack2": "Mandarin orange + graham crackers",
        },
        "Thu": {
            "lunch":  "Mild Japanese chicken curry rice",
            "dinner": "Mushroom & tofu nabe hotpot (mild) + rice",
            "snack1": "Tamagoyaki roll slices",
            "snack2": "Grapes + rice crackers",
        },
        "Fri": {
            "lunch":  "Salmon ochazuke (rice + warm green tea broth)",
            "dinner": "Yaki udon with chicken & cabbage",
            "snack1": "Edamame (lightly salted)",
            "snack2": "Apple + cheddar cheese slices",
        },
        "Sat": {
            "lunch":  "Kappa maki & tuna maki rolls",
            "dinner": "Mild sukiyaki (beef + mushrooms + tofu) + rice",
            "snack1": None, "snack2": None,
        },
        "Sun": {
            "lunch":  "Tamago gohan (egg on rice) + miso soup",
            "dinner": "Chicken katsu don (mild tonkatsu sauce)",
            "snack1": None, "snack2": None,
        },
    },
    2: {
        "Mon": {
            "lunch":  "Salmon onigiri + cup miso soup",
            "dinner": "Oyakodon (chicken & egg on rice)",
            "snack1": "Hard-boiled egg + rice crackers",
            "snack2": "Mandarin orange + babybel cheese",
        },
        "Tue": {
            "lunch":  "Zaru soba (cold) with sesame dipping sauce",
            "dinner": "Spinach & tofu miso hotpot + rice",
            "snack1": "Carrot sticks + cream cheese",
            "snack2": "Banana + graham crackers",
        },
        "Wed": {
            "lunch":  "Chahan (fried rice) with chicken, egg & peas",
            "dinner": "Grilled mackerel + pickled daikon + miso soup + rice",
            "snack1": "Tamagoyaki + cherry tomatoes",
            "snack2": "Apple + rice crackers",
        },
        "Thu": {
            "lunch":  "Udon soup with wakame seaweed & fish cake",
            "dinner": "Hambagu (Japanese hamburger steak) + spinach salad + rice",
            "snack1": "Edamame (lightly salted)",
            "snack2": "Grapes + cheddar cubes",
        },
        "Fri": {
            "lunch":  "Chirashi bowl (salmon, tamagoyaki, cucumber, rice)",
            "dinner": "Mild tonkotsu ramen with chashu pork & soft egg",
            "snack1": "Tuna mayo onigiri",
            "snack2": "Apple slices + crackers",
        },
        "Sat": {
            "lunch":  "Yaki onigiri (grilled rice balls) + clear soup",
            "dinner": "Shabu-shabu (pork belly + spinach + mushrooms) with sesame sauce",
            "snack1": None, "snack2": None,
        },
        "Sun": {
            "lunch":  "Zosui (egg & mushroom rice porridge)",
            "dinner": "Mild chicken curry udon",
            "snack1": None, "snack2": None,
        },
    },
    3: {
        "Mon": {
            "lunch":  "Chicken soboro don (minced chicken on rice)",
            "dinner": "Bok choy & tofu clear soup + miso-glazed chicken thigh + rice",
            "snack1": "Cucumber sticks + hummus",
            "snack2": "Banana + rice crackers",
        },
        "Tue": {
            "lunch":  "Tamagoyaki sandwich (Japanese egg roll in soft bread)",
            "dinner": "Gyudon (mild beef rice bowl) + steamed broccolini",
            "snack1": "Edamame (lightly salted)",
            "snack2": "Apple + babybel cheese",
        },
        "Wed": {
            "lunch":  "Salmon & tuna mayo onigiri duo + miso soup",
            "dinner": "Chicken & bok choy stir-fry + steamed rice",
            "snack1": "Plain salt onigiri",
            "snack2": "Mandarin orange + graham crackers",
        },
        "Thu": {
            "lunch":  "Udon with mushrooms, egg & chicken in dashi broth",
            "dinner": "Mild beef nikujaga (potato & beef stew) + rice",
            "snack1": "Tamagoyaki roll slices",
            "snack2": "Grapes + cheddar cubes",
        },
        "Fri": {
            "lunch":  "Chicken teriyaki rice bowl",
            "dinner": "Soba noodles with chicken & broccolini in dashi broth",
            "snack1": "Rice crackers + cheese",
            "snack2": "Apple slices + crackers",
        },
        "Sat": {
            "lunch":  "Temaki handrolls (salmon, cucumber, tamagoyaki)",
            "dinner": "Sukiyaki (beef + mushrooms + bok choy + tofu) + rice",
            "snack1": None, "snack2": None,
        },
        "Sun": {
            "lunch":  "Omurice (omelette fried rice, mild tomato-ketchup sauce)",
            "dinner": "Mild beef curry rice",
            "snack1": None, "snack2": None,
        },
    },
    4: {
        "Mon": {
            "lunch":  "Prawn & avocado rice bowl with sesame soy dressing",
            "dinner": "Pork mince & silken tofu stir-fry (mild) + rice + miso soup",
            "snack1": "Apple slices + cheddar",
            "snack2": "Rice crackers + hummus",
        },
        "Tue": {
            "lunch":  "Cold soba with prawn & cucumber in sesame sauce",
            "dinner": "Pan-fried pork & cabbage gyoza + steamed rice",
            "snack1": "Cucumber sticks + cream cheese",
            "snack2": "Banana + graham crackers",
        },
        "Wed": {
            "lunch":  "Chahan (fried rice) with egg, peas & carrot",
            "dinner": "Pork mince soboro don + miso soup",
            "snack1": "Plain salt onigiri",
            "snack2": "Mandarin orange + cheese",
        },
        "Thu": {
            "lunch":  "Udon soup with prawn, fish cake & carrot",
            "dinner": "Mild pork & potato Japanese curry + rice",
            "snack1": "Tamagoyaki roll slices",
            "snack2": "Grapes + rice crackers",
        },
        "Fri": {
            "lunch":  "Chirashi bowl (prawn, cucumber, tamagoyaki, sushi rice)",
            "dinner": "Cabbage & carrot okonomiyaki (mild) + miso soup",
            "snack1": "Edamame (lightly salted)",
            "snack2": "Apple + crackers",
        },
        "Sat": {
            "lunch":  "Prawn & cucumber temaki handrolls",
            "dinner": "Shabu-shabu (pork belly + cabbage + carrot + mushrooms) with sesame dip",
            "snack1": None, "snack2": None,
        },
        "Sun": {
            "lunch":  "Tamago gohan (egg on rice) + miso soup",
            "dinner": "Mild pork katsu udon",
            "snack1": None, "snack2": None,
        },
    },
}

# ─── GROCERY LISTS ───────────────────────────────────────────────────────────
# Quantities consolidate all cross-meal shared uses so you buy once.

GROCERY_LISTS = {
    1: {
        "🥦 Produce": [
            "Cucumber x3  (Tue sunomono, Tue snack, Sat maki)",
            "Cabbage 1/2 head  (Wed karaage garnish, Fri udon)",
            "Green onion / negi x1 bunch",
            "Shiitake mushrooms x1 pack  (Thu nabe, Sat sukiyaki)",
            "Shimeji mushrooms x1 pack  (Thu nabe, Sat sukiyaki)",
            "Apples x4", "Mandarin oranges x4", "Grapes x1 bunch", "Bananas x4",
            "Frozen edamame x1 bag",
        ],
        "🥩 Meat / Seafood": [
            "Chicken thigh fillet x800g  (Mon, Wed, Thu, Fri, Sun)",
            "Salmon fillet x400g  (Mon dinner + Fri ochazuke)",
            "Pork mince x300g  (Tue gyoza)",
            "Beef sukiyaki slices x300g  (Sat sukiyaki)",
            "Fish cake / narutomaki x1 pack  (Tue udon)",
        ],
        "🧊 Chilled": [
            "Eggs x10  (Wed soba, Thu tamagoyaki snack, Sun tamago gohan, miso soups)",
            "Silken tofu x2 packs  (Mon miso soup, Thu nabe, Sat sukiyaki)",
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
            "Spinach x2 bags  (Tue hotpot, Thu salad, Sat shabu-shabu)",
            "Daikon x1 small  (Wed pickled, Sat side)",
            "Carrot x2", "Cucumber x2",
            "Cherry tomatoes x1 punnet  (Wed snack)",
            "Green onion x1 bunch",
            "Shiitake x1 pack  (Sat shabu, Sun zosui)",
            "Shimeji x1 pack  (Sat shabu, Sun zosui)",
            "Frozen peas x1 bag  (Wed chahan)",
            "Frozen edamame x1 bag  (Thu snack)",
            "Apples x4", "Mandarin oranges x4", "Grapes x1 bunch", "Bananas x4",
        ],
        "🥩 Meat / Seafood": [
            "Chicken thigh fillet x400g  (Mon oyakodon, Wed chahan, Sun curry udon)",
            "Salmon fillet x300g  (Mon onigiri + Fri chirashi)",
            "Pork belly x500g  (Fri chashu ramen + Sat shabu-shabu)",
            "Minced pork + beef mix x300g  (Thu hambagu)",
            "Mackerel fillets x2  (Wed dinner)",
            "Fish cake x1 pack  (Thu udon)",
        ],
        "🧊 Chilled": [
            "Eggs x12  (Mon oyakodon, Wed tamagoyaki, Fri chirashi, Sun zosui)",
            "Silken tofu x2 packs  (Tue hotpot, miso soups)",
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
            "Mild tonkatsu sauce",
            "Nori sheets x1 pack",
            "Rice crackers x2 packs", "Graham crackers x1 box",
            "Sushi rice vinegar", "Canned tuna x1",
        ],
    },
    3: {
        "🥦 Produce": [
            "Bok choy x2 bunches  (Mon soup, Wed stir-fry, Sat sukiyaki)",
            "Broccolini x2 bunches  (Tue side, Fri soba)",
            "Cucumber x3  (Wed onigiri side, Sat handrolls, snacks)",
            "Potato x3  (Thu nikujaga)",
            "Green onion x1 bunch",
            "Shiitake x1 pack  (Thu udon, Thu stew, Sat sukiyaki)",
            "Shimeji x1 pack  (Thu udon, Sat sukiyaki)",
            "Frozen edamame x1 bag  (Tue snack)",
            "Apples x4", "Mandarin oranges x4", "Grapes x1 bunch", "Bananas x4",
        ],
        "🥩 Meat / Seafood": [
            "Chicken thigh fillet x600g  (Mon soboro, Wed stir-fry, Fri teriyaki+soba)",
            "Beef thinly sliced x500g  (Tue gyudon, Thu stew, Sat sukiyaki, Sun curry)",
            "Salmon fillet x300g  (Wed onigiri + Sat handrolls)",
        ],
        "🧊 Chilled": [
            "Eggs x10  (Mon soboro topping, Thu tamagoyaki, Thu udon, Sat handrolls, Sun omurice)",
            "Silken tofu x2 packs  (Mon soup, Sat sukiyaki, miso soups)",
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
            "Mild Japanese curry roux x1 box", "Mild tonkatsu sauce x1 bottle",
            "Nori sheets x1 pack",
            "Rice crackers x2 packs", "Graham crackers x1 box",
            "Canned tuna x2", "Sushi rice vinegar",
            "Ketchup (omurice sauce)",
        ],
    },
    4: {
        "🥦 Produce": [
            "Cabbage x1 head  (Tue gyoza, Fri okonomiyaki, Sat shabu-shabu)",
            "Carrot x4  (Wed chahan, Thu curry, Sat shabu-shabu)",
            "Cucumber x3  (Tue soba, Fri chirashi, Sat handrolls)",
            "Avocado x1  (Mon bowl)",
            "Potato x3  (Thu pork curry)",
            "Green onion x1 bunch",
            "Shiitake x1 pack  (Sat shabu-shabu)",
            "Shimeji x1 pack  (Sat shabu-shabu)",
            "Frozen peas x1 bag  (Wed chahan)",
            "Frozen edamame x1 bag  (Fri snack)",
            "Apples x4", "Mandarin oranges x4", "Grapes x1 bunch", "Bananas x4",
        ],
        "🥩 Meat / Seafood": [
            "Prawns x500g peeled  (Mon, Tue, Thu, Fri, Sat)",
            "Pork mince x500g  (Tue gyoza, Wed soboro, Thu curry)",
            "Pork belly / shoulder x400g  (Sat shabu-shabu, Sun katsu)",
            "Fish cake x1 pack  (Thu udon)",
        ],
        "🧊 Chilled": [
            "Eggs x10  (Wed chahan, Thu tamagoyaki, Fri chirashi, Sun tamago gohan)",
            "Silken tofu x2 packs  (Mon stir-fry, miso soups)",
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

# ─── RECIPE OF THE WEEK — scraped + curated fallback ─────────────────────────

# 8 curated recipes (one per slot so it takes 8 weeks to repeat even without scraping)
CURATED_RECIPES = [
    {
        "name":   "Teriyaki Chicken Rice Bowl",
        "steps":  "Pan-fry chicken thigh 5 min each side. Mix 2 tbsp soy + 1 tbsp mirin + 1 tsp sugar, pour over, glaze 1 min. Slice and serve over rice.",
        "source": "https://www.justonecookbook.com/chicken-teriyaki/",
    },
    {
        "name":   "Oyakodon (Chicken & Egg Rice Bowl)",
        "steps":  "Simmer sliced chicken in 150ml dashi + 2 tbsp soy + 2 tbsp mirin 5 min. Pour beaten eggs over, cover 1 min until just set. Slide onto hot rice.",
        "source": "https://www.justonecookbook.com/oyakodon/",
    },
    {
        "name":   "Gyudon (Mild Beef Rice Bowl)",
        "steps":  "Simmer thin beef + half an onion in dashi + soy + mirin + sugar 8 min. Serve over rice; top with a soft-boiled egg.",
        "source": "https://www.justonecookbook.com/gyudon/",
    },
    {
        "name":   "Prawn Chirashi Bowl",
        "steps":  "Season rice with 2 tbsp rice vinegar + 1 tbsp sugar + 1/2 tsp salt. Top with cooked prawns, tamagoyaki strips, cucumber, nori and sesame. Done in 20 min.",
        "source": "https://japan.recipetineats.com/chirashi-sushi/",
    },
    {
        "name":   "Chicken Soboro Don",
        "steps":  "Stir-fry minced chicken with soy + mirin + sugar 5 min until crumbly. Serve over rice with scrambled egg alongside — kids love the sweet savory combo.",
        "source": "https://www.justonecookbook.com/soboro-don/",
    },
    {
        "name":   "Hambagu (Japanese Hamburger Steak)",
        "steps":  "Mix mince with grated onion, egg and panko. Shape into patties, pan-fry 4 min each side. Pour a quick soy + mirin + water sauce and simmer 2 min.",
        "source": "https://www.justonecookbook.com/hamburger-steak/",
    },
    {
        "name":   "Tamagoyaki (Japanese Rolled Omelette)",
        "steps":  "Beat 3 eggs with 1 tbsp dashi + 1 tsp soy + 1 tsp mirin + 1 tsp sugar. Pour in thirds into an oiled pan, rolling each layer into a log. Slice and serve.",
        "source": "https://www.justonecookbook.com/tamagoyaki/",
    },
    {
        "name":   "Salmon & Avocado Rice Bowl",
        "steps":  "Season hot rice with sushi vinegar. Top with pan-seared or raw salmon, sliced avocado, cucumber and nori. Drizzle with soy + sesame oil. Ready in 15 min.",
        "source": "https://chopstickchronicles.com/salmon-bowl/",
    },
]

# Keywords that flag a recipe as unsuitable for young children
_SKIP_WORDS = [
    "spicy", "chili", "chilli", "kimchi", "wasabi", "tobasco",
    "sriracha", "curry paste", "hot sauce", "gochujang",
]


def scrape_justonecookbook(week_num: int) -> dict | None:
    """Scrape Just One Cookbook's recent recipes page and pick one by week number.
    Returns None on any error so the caller can fall back to the curated list."""
    url = "https://www.justonecookbook.com/category/recipes/"
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; FamilyMealBot/1.0)"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="replace")

        # Recipe titles + URLs live in <h2 class="entry-title"><a href="...">...</a></h2>
        pattern = (
            r'<h2[^>]*class="[^"]*entry-title[^"]*"[^>]*>\s*'
            r'<a\s+href="(https://www\.justonecookbook\.com/[^"]+)"[^>]*>'
            r'([^<]+)</a>'
        )
        matches = re.findall(pattern, html, re.IGNORECASE | re.DOTALL)
        if not matches:
            log.info("JOC scrape: no recipe matches found in HTML.")
            return None

        safe = [
            (url.strip(), title.strip())
            for url, title in matches
            if not any(kw in title.lower() for kw in _SKIP_WORDS)
        ]
        if not safe:
            return None

        pick_url, pick_name = safe[week_num % len(safe)]
        log.info(f"Scraped recipe: {pick_name}")
        return {
            "name":   pick_name,
            "steps":  "Tap the link for full step-by-step instructions.",
            "source": pick_url,
        }

    except (urllib.error.URLError, Exception) as exc:
        log.info(f"JOC scrape failed ({exc}) — will use curated recipe.")
        return None


def get_recipe_of_week(iso_week: int, slot: int) -> dict:
    """Try scraping a fresh recipe; fall back to the 8-item curated list."""
    scraped = scrape_justonecookbook(iso_week)
    if scraped:
        return scraped
    # Curated list has 8 entries; use iso_week so it advances every week
    return CURATED_RECIPES[iso_week % len(CURATED_RECIPES)]


# ─── FORMATTERS ──────────────────────────────────────────────────────────────

DAY_EMOJIS = ["🌱", "🌿", "🍃", "🌾", "🎋", "🌸", "🌺"]


def get_week_slot(for_date: date) -> int:
    slot = for_date.isocalendar()[1] % 4
    return slot if slot != 0 else 4


def format_meal_plan(slot: int, week_start: date, recipe: dict) -> str:
    plan     = MEAL_PLANS[slot]
    week_end = week_start + timedelta(days=6)
    lines = [
        f"🍱 *FAMILY MEAL PLAN — Rotation {slot}/4*",
        f"📆 {week_start.strftime('%d %b')} – {week_end.strftime('%d %b %Y')}",
        "",
    ]
    for i, day in enumerate(DAYS):
        day_date     = week_start + timedelta(days=i)
        d            = plan[day]
        is_school_day = i < 5
        lines.append(f"{DAY_EMOJIS[i]} *{day} {day_date.strftime('%d %b')}*")
        lines.append(f"  🥗 Lunch: {d['lunch']}")
        lines.append(f"  🍽 Dinner: {d['dinner']}")
        if is_school_day:
            lines.append(f"  🏫 Recess: {d['snack1']}")
            lines.append(f"  🎒 Break: {d['snack2']}")
        lines.append("")
    lines += [
        "─" * 26,
        "👨‍🍳 *RECIPE OF THE WEEK*",
        f"_{recipe['name']}_",
        recipe["steps"],
        f"🔗 {recipe['source']}",
    ]
    return "\n".join(lines)


def format_grocery_list(slot: int, week_start: date) -> str:
    grocery = GROCERY_LISTS[slot]
    lines = [
        "🛒 *GROCERY LIST — FairPrice*",
        f"Week of {week_start.strftime('%d %b')} · qty covers all shared meals",
        "",
    ]
    for section, items in grocery.items():
        lines.append(f"*{section}*")
        for item in items:
            lines.append(f"  • {item}")
        lines.append("")
    lines.append("✅ _Happy shopping! Tick off as you go_ 🧺")
    return "\n".join(lines)


# ─── SESSION RESTORE (matches family_bot_cloud.py exactly) ───────────────────

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


# ─── WHATSAPP SENDER (matches family_bot_cloud.py exactly) ───────────────────

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

            log.info("Waiting for chat list (up to 90s)...")
            try:
                await page.wait_for_selector(
                    '[data-testid="chat-list"], [aria-label="Chat list"], ._aigw, #pane-side',
                    timeout=90000,
                )
                log.info("Chat list loaded.")
            except Exception:
                await page.screenshot(path="debug_01_no_chatlist.png", full_page=True)
                raise RuntimeError("WhatsApp session invalid — re-run login_exporter.py and recommit session_encrypted.zip.")

            await asyncio.sleep(5)

            # Navigate to the group via invite code (same as family_bot_cloud.py)
            log.info(f"Navigating to group: {group_url}")
            await page.goto(group_url, timeout=30000, wait_until="domcontentloaded")
            await asyncio.sleep(5)
            await page.screenshot(path="debug_02_group.png", full_page=True)

            # Find compose box
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
                raise RuntimeError("Could not reach compose box — check debug screenshots.")

            # Send each message
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
    if today.weekday() == 5:          # Saturday → plan for NEXT week
        week_start = today + timedelta(days=2)
    else:                             # any other day → current week
        week_start = today - timedelta(days=today.weekday())

    iso_week = week_start.isocalendar()[1]
    slot     = get_week_slot(week_start)
    log.info(f"Today: {today} | Week start: {week_start} | ISO week: {iso_week} | Slot: {slot}/4")

    recipe      = get_recipe_of_week(iso_week, slot)
    meal_msg    = format_meal_plan(slot, week_start, recipe)
    grocery_msg = format_grocery_list(slot, week_start)

    log.info("\n─── MEAL PLAN ───\n" + meal_msg)
    log.info("\n─── GROCERY LIST ───\n" + grocery_msg)

    if os.environ.get("SESSION_PASSWORD"):
        asyncio.run(send_whatsapp([meal_msg, grocery_msg]))
        log.info("=== Bot finished successfully ===")
    else:
        log.info("SESSION_PASSWORD not set — dry run only, no messages sent.")


if __name__ == "__main__":
    main()
