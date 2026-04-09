# 📅 Family Calendar WhatsApp Bot

Fetches tomorrow's Google Calendar events and sends a formatted summary to the family WhatsApp group — automated via GitHub Actions, no PC required.

- **Scheduling**: GitHub Actions cron (11:58 UTC = 19:58 SGT)
- **WhatsApp**: Playwright + headless Chromium (no Meta Business API)
- **Session security**: WhatsApp session AES-256 encrypted, committed to repo

---

## 🗂️ Project Structure

| File | Purpose |
|---|---|
| `family_bot_cloud.py` | Main bot — runs in GitHub Actions |
| `login_exporter.py` | Local tool — captures & encrypts WhatsApp session |
| `generate_token.py` | Local tool — generates/refreshes Google `token.json` |
| `session_encrypted.zip` | Encrypted WhatsApp session committed to repo |
| `.github/workflows/daily_bot.yml` | GitHub Actions workflow |

---

## 🔐 GitHub Secrets Required

| Secret | Description |
|---|---|
| `SESSION_PASSWORD` | Password used to encrypt `session_encrypted.zip` |
| `GOOGLE_CREDENTIALS` | Full contents of `credentials.json` |
| `GOOGLE_TOKEN` | Full contents of `token.json` |

---

## 🚀 First-Time Setup

### 1. Google Calendar API

1. Enable the Google Calendar API in [Google Cloud Console](https://console.cloud.google.com).
2. Configure the OAuth Consent Screen (Testing mode).
3. Add `familieng9@gmail.com` as a Test User.
4. Download the Desktop Client Secret and save as `credentials.json` in this folder.
5. Run once to generate `token.json`:
   ```
   python generate_token.py
   ```
6. Add both files as GitHub Secrets (`GOOGLE_CREDENTIALS`, `GOOGLE_TOKEN`).

> ⚠️ Do NOT run `gcloud auth application-default login` — the bot uses `token.json` directly.

### 2. WhatsApp Session

Run `login_exporter.py` locally (see **Refreshing the WhatsApp Session** below) and commit the output.

### 3. Configuration

All config is at the top of `family_bot_cloud.py`:

- **WhatsApp group**: `GROUP_INVITE_CODE = "FHQ7HrFjHEOJQ3fbnl84UC"`
- **Calendars checked**:
  - `primary` (Main email)
  - `0gs624o1448ja48f0ielplj9co@group.calendar.google.com` (Tessa x Popo)
  - `family07313615549286623759@group.calendar.google.com` (Family)
- **Timezone**: Asia/Singapore (SGT, UTC+8)

---

## 🔄 Refreshing the WhatsApp Session

The WhatsApp session stored in `session_encrypted.zip` will expire periodically (typically every few weeks). When it does, the GitHub Actions run will fail with:

```
RuntimeError: WhatsApp session invalid. Re-run login_exporter.py and recommit session_encrypted.zip.
```

**To refresh:**

1. Run locally:
   ```
   python login_exporter.py
   ```
2. A Chromium window will open — scan the QR code with your phone.
3. Once chats are visible, press Enter. Enter your encryption password when prompted.
4. Commit and push the new `session_encrypted.zip`:
   ```
   git add session_encrypted.zip
   git commit -m "Refresh WhatsApp session"
   git push
   ```
5. Update the `SESSION_PASSWORD` GitHub Secret if you changed the password:
   ```
   gh secret set SESSION_PASSWORD --repo familieng9/familycalendarbot
   ```

---

## 🛠️ Local Setup (for running login_exporter.py)

```
pip install playwright pyzipper google-api-python-client google-auth-httplib2 google-auth-oauthlib pytz
playwright install chromium
```

---

## 🐛 Debugging Failed Runs

When a GitHub Actions run fails, screenshots are automatically uploaded as an artifact called `debug-screenshots`. Download them from the Actions run page to see exactly what state the browser was in.