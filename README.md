# 📅 Family Calendar Bot

A collection of WhatsApp automation bots that run via GitHub Actions — no PC required.

---

## 🤖 Bots

### 1. Family Calendar Bot
Fetches tomorrow's Google Calendar events and sends a daily schedule to the family WhatsApp group.
- **Schedule**: Every day at 19:58 SGT
- **Script**: `family_bot_cloud.py`

### 2. SG Kids Activities Bot
Scrapes SassyMama & SunnyCityKids for weekend kids activities in Singapore and sends 3 messages to the family WhatsApp group.
- **Schedule**: Every Thursday at 17:00 SGT
- **Script**: `kids_activities_bot.py`

---

## 🗂️ Project Structure

| File | Purpose |
|---|---|
| `family_bot_cloud.py` | Daily calendar bot |
| `kids_activities_bot.py` | Weekly kids activities bot |
| `login_exporter.py` | Local tool — captures & encrypts WhatsApp session |
| `generate_token.py` | Local tool — generates/refreshes Google `token.json` |
| `session_encrypted.zip` | Encrypted WhatsApp session committed to repo |
| `.github/workflows/daily_bot.yml` | Daily calendar workflow |
| `.github/workflows/kids_activities.yml` | Weekly kids activities workflow |

---

## 🔐 GitHub Secrets Required

| Secret | Used By | Description |
|---|---|---|
| `SESSION_PASSWORD` | Both bots | Password used to encrypt `session_encrypted.zip` |
| `GOOGLE_CREDENTIALS` | Calendar bot | Full contents of `credentials.json` |
| `GOOGLE_TOKEN` | Calendar bot | Full contents of `token.json` |

---

## 🚀 First-Time Setup

### 1. Google Calendar API (Calendar bot only)

1. Enable the Google Calendar API in [Google Cloud Console](https://console.cloud.google.com).
2. Configure the OAuth Consent Screen (Testing mode).
3. Add `familieng9@gmail.com` as a Test User.
4. Download the Desktop Client Secret and save as `credentials.json` in this folder.
5. Run once to generate `token.json`:
   ```
   python generate_token.py
   ```
6. Add both files as GitHub Secrets (`GOOGLE_CREDENTIALS`, `GOOGLE_TOKEN`):
   ```
   cmd /c "gh secret set GOOGLE_CREDENTIALS --repo familieng9/familycalendarbot < credentials.json"
   cmd /c "gh secret set GOOGLE_TOKEN --repo familieng9/familycalendarbot < token.json"
   ```

> ⚠️ Do NOT run `gcloud auth application-default login` — the bot uses `token.json` directly.

> ⚠️ Set the OAuth consent screen to **In production** (not Testing) to prevent refresh tokens expiring after 7 days.

### 2. WhatsApp Session

Run `login_exporter.py` locally (see **Refreshing the WhatsApp Session** below) and commit the output.

### 3. Configuration

Config is at the top of each script:

- **WhatsApp group**: `GROUP_INVITE_CODE = "FHQ7HrFjHEOJQ3fbnl84UC"`
- **Calendars checked** (calendar bot):
  - `primary` (Main email)
  - `0gs624o1448ja48f0ielplj9co@group.calendar.google.com` (Tessa x Popo)
  - `family07313615549286623759@group.calendar.google.com` (Family)
- **Activity sources** (kids activities bot): SassyMama, SunnyCityKids
- **Timezone**: Asia/Singapore (SGT, UTC+8)

---

## 🔄 Refreshing the WhatsApp Session

The WhatsApp session in `session_encrypted.zip` expires periodically (every few weeks). When it does, the GitHub Actions run will fail with:

```
RuntimeError: WhatsApp session invalid. Re-run login_exporter.py and recommit session_encrypted.zip.
```

**To refresh:**

1. Run locally:
   ```
   python login_exporter.py
   ```
2. A Chromium window will open — scan the QR code with your phone.
3. Wait until chats are fully visible and scrollable, then press Enter.
4. Enter your encryption password when prompted.
5. Commit and push the new `session_encrypted.zip`:
   ```
   git add session_encrypted.zip
   git commit -m "Refresh WhatsApp session"
   git push origin main
   ```
6. Update `SESSION_PASSWORD` secret if you changed the password:
   ```
   gh secret set SESSION_PASSWORD --repo familieng9/familycalendarbot
   ```

---

## 🔑 Refreshing the Google Token

If the calendar bot fails with `invalid_grant`, re-run:

```
python generate_token.py
```

Then update the secret:
```
cmd /c "gh secret set GOOGLE_TOKEN --repo familieng9/familycalendarbot < token.json"
```

---

## 🛠️ Local Setup

```
pip install playwright pyzipper google-api-python-client google-auth-httplib2 google-auth-oauthlib pytz
playwright install chromium
```

---

## 🐛 Debugging Failed Runs

When a run fails, screenshots are automatically uploaded as artifacts. Download them from the Actions run page:
- Calendar bot failures → artifact: `debug-screenshots`
- Kids activities bot failures → artifact: `kids-debug-screenshots`
