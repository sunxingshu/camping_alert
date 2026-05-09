# Setup Guide — Camping Alert System

## Prerequisites

- Python 3.11 or newer
- Git

---

## Step 1 — Install dependencies

```bash
cd camping_alert
pip install -e ".[dev]"

# Install Playwright's headless browser (needed for Hipcamp scraping)
playwright install chromium
```

---

## Step 2 — Get a Gmail App Password

The alert emails are sent via your Gmail account using an App Password
(not your regular password). This is required when 2-Factor Authentication
is enabled (which Google now requires).

1. Go to **myaccount.google.com** → Sign in
2. Click **Security** in the left menu
3. Under "How you sign in to Google," click **2-Step Verification** and enable it
4. After enabling 2FA, go back to Security and search for **App Passwords**
   (or go directly to: myaccount.google.com/apppasswords)
5. Under "Select app" choose **Mail**
6. Under "Select device" choose **Other** → type `camping-alert` → click Generate
7. Copy the 16-character password (it looks like: `abcd efgh ijkl mnop`)

---

## Step 3 — Configure your `.env` file

Copy the example and fill it in:

```bash
cp .env.example .env
```

Open `.env` and set:

```ini
ALERT_FROM_EMAIL=sunxingshu@gmail.com
ALERT_FROM_PASSWORD=abcdefghijklmnop    # your 16-char app password (no spaces)
ALERT_TO_EMAIL=sunxingshu@gmail.com     # send alerts to yourself

# Everything else can stay as-is for now
```

---

## Step 4 — (Optional) Recreation.gov API Key

Recreation.gov's **public availability API does not require a key** — the
checker works without one. If you ever hit rate limits, you can register
for a free key:

1. Go to **recreation.gov** → create a free account
2. Visit **recreation.gov/use-our-data** → request a Data API key
3. Add to `.env`:
   ```ini
   RECGOV_API_KEY=your_key_here
   ```

---

## Step 5 — Run a single test check

This checks all platforms once, prints results, and exits. No emails are
sent unless a real slot is found.

```bash
python -m camping_alert.main --once
```

You'll see output like:

```
2026-05-09 14:00:01 [INFO] Checking ReserveCalifornia: Seacliff State Beach
2026-05-09 14:00:04 [INFO] Checking ReserveCalifornia: New Brighton State Beach
...
2026-05-09 14:00:45 [INFO] Check complete. 0 new alerts sent.
```

---

## Step 6 — Run continuously

```bash
python -m camping_alert.main
```

The monitor will:
- Check every **15 minutes** (Mon–Wed, Sun)
- Check every **5 minutes** on Thursday and Friday (when campgrounds
  release new dates)
- Only check between **6 AM – 11 PM Pacific** to avoid overnight noise
- Send you an email the moment a qualifying spot opens up

---

## Step 7 — Keep it running (optional: auto-restart)

### On macOS (launchd):

Create `~/Library/LaunchAgents/com.camping-alert.plist` with:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.camping-alert</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/local/bin/python</string>
    <string>-m</string>
    <string>camping_alert.main</string>
  </array>
  <key>WorkingDirectory</key>
  <string>/path/to/camping_alert</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>/tmp/camping-alert.log</string>
  <key>StandardErrorPath</key>
  <string>/tmp/camping-alert-error.log</string>
</dict>
</plist>
```

Then: `launchctl load ~/Library/LaunchAgents/com.camping-alert.plist`

### On Linux (systemd or cron):

```bash
# cron: run every 15 min, redirect logs
*/15 * * * * cd /path/to/camping_alert && python -m camping_alert.main --once >> /var/log/camping-alert.log 2>&1
```

---

## Campgrounds being monitored

| Park | Location | Hookup | Drive |
|---|---|---|---|
| Doran Regional Park | Bodega Bay, CA | **Full (W/E/S)** ⭐ | ~1h 55m |
| Seacliff State Beach | Aptos, CA | Electric + dump | ~1h 10m |
| New Brighton State Beach | Capitola, CA | Electric + dump | ~1h 10m |
| Bodega Dunes (Sonoma Coast) | Bodega Bay, CA | Dump only | ~1h 55m |
| Half Moon Bay SB (Francis Beach) | Half Moon Bay, CA | Dump only | ~45m |
| Manresa State Beach | La Selva Beach, CA | Dump only | ~1h 15m |
| Sunset State Beach | Watsonville, CA | Dump only | ~1h 20m |
| Kirby Cove (GGNRA) | Sausalito, CA | None | ~1h 5m |
| Hipcamp coastal search | Half Moon Bay → Bodega Bay | Varies | Varies |

---

## Troubleshooting

**No alerts arriving:**
- Run `--once` and check the log output for errors
- Verify your Gmail App Password is correct (no spaces, 16 chars)
- Check your spam folder

**"Outside active hours" log message:**
- Normal — the monitor skips checks between 11 PM and 6 AM Pacific

**ReserveCalifornia returning no results:**
- Their API occasionally changes; check the GitHub repo for updates
- Try running `--once` during business hours (9 AM–5 PM PT) for best results

**Hipcamp not finding sites:**
- Hipcamp blocks automated requests intermittently; this is expected
- Recreation.gov and ReserveCalifornia are the primary sources
