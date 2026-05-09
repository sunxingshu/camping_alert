# Camping Availability Alert System — Requirements

## 1. Project Overview

A scheduled monitoring service that scrapes campsite availability from
Recreation.gov and ReserveAmerica, filters for sites matching specific
criteria, and sends email alerts the moment a qualifying site opens up.

**Goal:** Never miss a coastal campsite opening for a Friday–Saturday weekend
trip within 2 hours of Newark, CA.

---

## 2. Trip Criteria (the "golden filter")

| Criteria | Requirement |
|---|---|
| Departure city | Newark, CA (37.5296° N, 122.0402° W) |
| Max drive time | 2 hours (≈ 120 miles radius, traffic-adjusted) |
| Coastline | Pacific Ocean — site must have ocean access or ocean view |
| Nights | Friday night + Saturday night (both must be available) |
| Site type | Full hookup (W/E/S) OR partial hookup (W/E or E only) **with** on-site dump station |
| Slide-out clearance | Pull-through or back-in with ≥ 25 ft usable length |
| Booking windows | Check 2–12 weeks out from current date (rolling) |

---

## 3. Target Campgrounds

The following coastal campgrounds fall within the 2-hour drive window and have
hookup or partial-hookup sites. This list is the initial seed; new campgrounds
can be added via config.

### 3a. ReserveAmerica (California State Parks)

| Park | City | Drive from Newark | Hookup Type | Notes |
|---|---|---|---|---|
| Seacliff State Beach | Aptos, CA | ~1 hr 10 min | Electric (partial) | Dump station on-site; pull-through sites available |
| New Brighton State Beach | Capitola, CA | ~1 hr 10 min | Electric (partial) | Dump station on-site |
| Sunset State Beach | Watsonville, CA | ~1 hr 20 min | No hookups | Dump station only — include if dump station qualifies |
| Manresa State Beach | La Selva Beach, CA | ~1 hr 10 min | No hookups | Primitive; skip unless criteria relaxed |
| Half Moon Bay SB (Francis Beach) | Half Moon Bay, CA | ~45 min | No hookups | Closest — monitor for dump station sites |
| Bodega Dunes (Sonoma Coast SB) | Bodega Bay, CA | ~1 hr 55 min | No hookups | Dump station; large pull-through sites |
| Doran Regional Park | Bodega Bay, CA | ~1 hr 55 min | Full hookup (W/E/S) | **Best match** — full hookup, pull-through |
| Westport-Union Landing SB | Westport, CA | ~2 hr 30 min | No hookups | Outside range — exclude |

### 3b. Recreation.gov (Federal / County)

| Park | City | Drive from Newark | Hookup Type | Notes |
|---|---|---|---|---|
| Kirby Cove Campground | Sausalito, CA | ~1 hr 10 min | No hookups | Scenic but primitive |
| Samuel P. Taylor SP | Lagunitas, CA | ~1 hr 20 min | Partial | Not oceanfront but near coast |
| Steep Ravine (Mt. Tamalpais) | Stinson Beach, CA | ~1 hr 25 min | No hookups | Cabins/primitive |
| Point Reyes (Coast Camp) | Inverness, CA | ~1 hr 40 min | No hookups | Hike-in; skip |

> **Priority sites for MVP:** Seacliff, New Brighton, Doran Regional Park.
> These three have the highest hookup/dump-station coverage closest to Newark.

---

## 4. Monitoring Sources

### 4a. Recreation.gov
- **API:** Public availability API at `https://www.recreation.gov/api/camps/`
- **Authentication:** Requires a free API key (register at recreation.gov)
- **Rate limit:** ~1 req/sec; use exponential backoff
- **Availability endpoint:** `GET /api/camps/availability/campground/{campground_id}/month`
- **Response:** Returns per-site, per-date availability slots

### 4b. ReserveAmerica (California State Parks)
- **URL:** `https://www.reservecalifornia.com` (ReserveAmerica-powered)
- **Method:** Web scraping via headless browser (Playwright) or undocumented JSON API calls made by the website's frontend
- **Rate limiting:** Polite scraping — minimum 3-second delay between requests
- **Fallback:** If scraping is blocked, use `reservecalifornia.com` search URL with query parameters

---

## 5. Site Matching Logic

A campsite is a match when **all** of the following are true:

```
1. campground is in the TARGET_CAMPGROUNDS list
2. site has electric hookup (W/E or W/E/S) OR campground has dump station
3. site pull-through length >= 25 ft  (if length data available)
   OR site type is labeled "pull-through" or "pull-in"
4. site is available on the user's target Friday night
5. site is available on the user's target Saturday night
   (both nights must be open — not just one)
6. site is bookable for 2+ nights (minimum stay not blocking)
```

If site length is not published by the booking platform, include the site and
flag it in the alert body as "length unconfirmed."

---

## 6. Alert System

### 6a. Trigger Conditions
- Alert fires on **first detection** of a new matching slot.
- Subsequent re-checks of the same site+date combination do NOT re-alert
  (deduplication via a local seen-slots database).
- If a previously alerted slot disappears and then reappears, re-alert.

### 6b. Email Alert Content

**Subject line:**
```
[CAMPING ALERT] {Site Name} at {Park} — Fri {date} + Sat {date} AVAILABLE
```

**Email body must include:**
- Park name and campsite number/name
- Hookup type (full / partial / dump-station only)
- Site length (if known)
- Pull-through or back-in
- Dates available
- Direct booking URL
- Drive time from Newark, CA (static lookup from config)
- Timestamp of detection

**Example:**
```
Site #47 (Pull-Through, 40 ft, Full Hookup W/E/S)
Doran Regional Park — Bodega Bay, CA
Drive from Newark, CA: ~1 hr 55 min

Available: Friday May 15 + Saturday May 16, 2026
Book now: https://www.reservecalifornia.com/...

Detected at: 2026-05-09 14:32 UTC
```

### 6c. Delivery
- **Protocol:** SMTP (configurable)
- **Default provider:** Gmail SMTP (smtp.gmail.com:587, STARTTLS)
- **Sender:** Configurable via env var `ALERT_FROM_EMAIL`
- **Recipient:** Configurable via env var `ALERT_TO_EMAIL`
- **Deduplication TTL:** 48 hours (re-alert if slot is still open after 48h)

---

## 7. Scheduling

| Parameter | Default | Description |
|---|---|---|
| Check interval | Every 15 minutes | How often to poll all campgrounds |
| Active hours | 06:00–23:00 Pacific | Skip overnight polling to stay polite |
| Weekend boost | Every 5 minutes | More frequent checks Thu–Fri when release windows open |
| Look-ahead window | 2–12 weeks | Date range to search for Fri+Sat pairs |

Scheduling is handled by a simple loop with `schedule` (Python) or a cron job.

---

## 8. Configuration (`.env` file)

```ini
# Email delivery
ALERT_FROM_EMAIL=your_sender@gmail.com
ALERT_FROM_PASSWORD=your_app_password
ALERT_TO_EMAIL=your_personal@email.com
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587

# Recreation.gov
RECGOV_API_KEY=your_key_here

# Search parameters
ORIGIN_LAT=37.5296
ORIGIN_LNG=-122.0402
MAX_DRIVE_MINUTES=120
LOOKAHEAD_WEEKS_MIN=2
LOOKAHEAD_WEEKS_MAX=12
CHECK_INTERVAL_MINUTES=15

# Site requirements
MIN_SITE_LENGTH_FT=25
REQUIRE_HOOKUP=true          # false = include no-hookup sites with dump station
REQUIRE_DUMP_STATION=true
```

---

## 9. Data Storage

- **SQLite** (single file `seen_slots.db`) — lightweight, no server required
- Schema:

```sql
CREATE TABLE seen_slots (
  id            TEXT PRIMARY KEY,   -- "{campground_id}:{site_id}:{checkin_date}"
  park_name     TEXT,
  site_name     TEXT,
  checkin_date  TEXT,
  checkout_date TEXT,
  alerted_at    TEXT,               -- ISO 8601 timestamp
  booking_url   TEXT
);
```

---

## 10. Technical Stack

| Layer | Choice | Reason |
|---|---|---|
| Language | Python 3.11+ | Strong scraping/HTTP ecosystem |
| HTTP client | `httpx` (async) | Async, modern, good for rate limiting |
| HTML scraping | `playwright` (headless Chromium) | JS-rendered pages on ReserveAmerica |
| Scheduling | `schedule` library | Simple cron-like, no daemon required |
| Email | `smtplib` + `email.mime` | Standard library, no extra deps |
| DB | `sqlite3` (stdlib) | Zero-config persistence |
| Config | `python-dotenv` | `.env` file support |
| Packaging | `pyproject.toml` + `pip` | Standard modern packaging |

---

## 11. Project File Structure

```
camping_alert/
├── REQUIREMENTS.md          ← this file
├── .env.example             ← template for secrets
├── .gitignore
├── pyproject.toml
├── README.md
├── src/
│   └── camping_alert/
│       ├── __init__.py
│       ├── main.py          ← entry point / scheduler loop
│       ├── config.py        ← loads .env, validates settings
│       ├── campgrounds.py   ← static list of target campgrounds + metadata
│       ├── checkers/
│       │   ├── __init__.py
│       │   ├── recgov.py    ← Recreation.gov API checker
│       │   └── reserveamerica.py  ← ReserveAmerica scraper
│       ├── matcher.py       ← applies the "golden filter" rules
│       ├── notifier.py      ← email alert sender
│       └── db.py            ← SQLite seen-slots store
└── tests/
    ├── test_matcher.py
    ├── test_notifier.py
    └── test_db.py
```

---

## 12. Out of Scope (v1)

- SMS/text alerts (can add Twilio in v2)
- Hipcamp or other platforms
- Automatic booking (legal gray area; alert-only is safer)
- Web UI or dashboard
- Docker packaging (run locally or on a cheap VPS)

---

## 13. Open Items / Decisions Needed

| # | Question | Owner |
|---|---|---|
| 1 | Confirm email address(es) for alerts | User |
| 2 | Confirm target dates (specific weekends or rolling?) | User |
| 3 | Recreation.gov API key — user must register | User |
| 4 | Should Bodega Bay (1h55m) be included even though it's close to the 2hr limit? | User |
| 5 | Acceptable to scrape ReserveAmerica? Their ToS discourages automated access | User |
| 6 | Run on user's own machine, a VPS, or a cloud function? | User |

---

## 14. Success Criteria

The system is working correctly when:

1. It runs continuously without crashing for 7 days
2. It detects a known-available site within 30 minutes of it opening
3. No duplicate alerts are sent for the same site+date
4. Email arrives within 60 seconds of detection
5. All target campgrounds are checked at least once per 15-minute cycle
