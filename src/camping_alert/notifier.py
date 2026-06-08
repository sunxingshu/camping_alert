"""Send email alerts for newly available campsites."""

import smtplib
import logging
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from .config import Config
from .matcher import AvailableSlot

log = logging.getLogger(__name__)


def _build_email(slot: AvailableSlot, cfg: Config) -> MIMEMultipart:
    hookup_label = {
        "full": "Full Hookup (Water + Electric + Sewer)",
        "partial": "Partial Hookup (Water + Electric)",
        "electric": "Electric Hookup",
        "none": "No Hookup",
    }.get(slot.hookup_type.value, slot.hookup_type.value)

    pull_label = (
        "Pull-Through" if slot.is_pull_through
        else "Back-In" if slot.is_pull_through is False
        else "Unknown (verify before booking)"
    )

    length_label = (
        f"{slot.site_length_ft} ft"
        if slot.site_length_ft
        else "Not published — verify before booking"
    )

    dump_label = "Yes" if slot.has_dump_station else "No"

    fri = slot.checkin.strftime("%A %b %d, %Y")
    sat = (slot.checkin.replace(day=slot.checkin.day + 1)).strftime("%A %b %d, %Y")
    detected = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    subject = (
        f"[CAMPING ALERT] {slot.site_name} @ {slot.campground.park_name} "
        f"— Fri {slot.checkin.strftime('%b %d')} + Sat available!"
    )

    plain = f"""
╔══════════════════════════════════════════════════════════╗
   COASTAL CAMPSITE AVAILABLE — BOOK NOW!
╚══════════════════════════════════════════════════════════╝

Site:          {slot.site_name}
Park:          {slot.campground.park_name}
Location:      {slot.campground.city}
Drive time:    ~{slot.campground.drive_minutes} min from Newark, CA
Ocean view:    {"YES 🌊" if slot.campground.ocean_view else "Near coast"}

Hookup:        {hookup_label}
Dump station:  {dump_label}
Site type:     {pull_label}
Site length:   {length_label}

Available:
  ✓ Friday    {fri}
  ✓ Saturday  {sat}

BOOK NOW:
  {slot.booking_url}

Notes:
  {slot.campground.notes or "None"}

Detected at: {detected}
──────────────────────────────────────────────────────────
This alert was sent by your camping-alert monitor.
It will not repeat for this site+date unless the slot
disappears and reappears.
""".strip()

    html = f"""
<html><body style="font-family:Arial,sans-serif;max-width:600px;margin:auto">
<div style="background:#1a6b3c;color:white;padding:16px;border-radius:8px 8px 0 0">
  <h2 style="margin:0">🏕️ Coastal Campsite Available!</h2>
  <p style="margin:4px 0 0">{slot.campground.park_name} &mdash; {slot.campground.city}</p>
</div>
<div style="border:1px solid #ccc;border-top:none;padding:16px;border-radius:0 0 8px 8px">
  <table style="width:100%;border-collapse:collapse">
    <tr><td style="padding:6px;color:#555;width:40%"><b>Site</b></td>
        <td style="padding:6px">{slot.site_name}</td></tr>
    <tr style="background:#f9f9f9">
        <td style="padding:6px;color:#555"><b>Drive from Newark, CA</b></td>
        <td style="padding:6px">~{slot.campground.drive_minutes} min</td></tr>
    <tr><td style="padding:6px;color:#555"><b>Ocean View</b></td>
        <td style="padding:6px">{"🌊 Yes" if slot.campground.ocean_view else "Near coast"}</td></tr>
    <tr style="background:#f9f9f9">
        <td style="padding:6px;color:#555"><b>Hookup</b></td>
        <td style="padding:6px">{hookup_label}</td></tr>
    <tr><td style="padding:6px;color:#555"><b>Dump Station</b></td>
        <td style="padding:6px">{dump_label}</td></tr>
    <tr style="background:#f9f9f9">
        <td style="padding:6px;color:#555"><b>Site Type</b></td>
        <td style="padding:6px">{pull_label}</td></tr>
    <tr><td style="padding:6px;color:#555"><b>Site Length</b></td>
        <td style="padding:6px">{length_label}</td></tr>
  </table>

  <div style="background:#e8f5e9;border-left:4px solid #1a6b3c;padding:12px;margin:16px 0;border-radius:4px">
    <b>Available nights:</b><br>
    ✅ Friday &nbsp;&nbsp;{fri}<br>
    ✅ Saturday {sat}
  </div>

  <a href="{slot.booking_url}"
     style="display:block;background:#1a6b3c;color:white;text-align:center;
            padding:14px;border-radius:6px;text-decoration:none;font-size:18px;font-weight:bold">
    BOOK NOW
  </a>

  {"<p style='color:#555;font-size:13px;margin-top:12px'><b>Notes:</b> " + slot.campground.notes + "</p>" if slot.campground.notes else ""}

  <p style="color:#999;font-size:12px;margin-top:16px;border-top:1px solid #eee;padding-top:8px">
    Detected at {detected}. This alert will not repeat for the same site+date
    unless the slot disappears and reappears.
  </p>
</div>
</body></html>
""".strip()

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = cfg.alert_from_email
    msg["To"] = cfg.alert_to_email
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html, "html"))
    return msg


def send_heartbeat(cfg: Config, campground_names: list[str], lookahead_weeks: int) -> None:
    """Send a weekly 'system alive' status email."""
    detected = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    cg_list = "\n".join(f"  • {n}" for n in campground_names)
    cg_list_html = "".join(f"<li>{n}</li>" for n in campground_names)

    subject = f"[Camping Alert] Weekly status — running, nothing to report ({detected[:10]})"

    plain = f"""
Camping Alert — Weekly Status
==============================

Your coastal campsite monitor is running normally.

Nothing new is being reported in this weekly status email. Daily scans will
still email you immediately if a campsite matching your filters opens up.

Monitoring {len(campground_names)} campgrounds ({lookahead_weeks} week lookahead):
{cg_list}

Outside this weekly status email, you will only receive emails when a site
matching your filters opens up (hookup, dump station, 25ft+ pull-through,
ocean adjacent).

Status sent at: {detected}
""".strip()

    html = f"""
<html><body style="font-family:Arial,sans-serif;max-width:560px;margin:auto">
<div style="background:#1a6b3c;color:white;padding:14px;border-radius:8px 8px 0 0">
  <h2 style="margin:0">Camping Alert — Weekly Status</h2>
</div>
<div style="border:1px solid #ccc;border-top:none;padding:16px;border-radius:0 0 8px 8px">
  <p>Your coastal campsite monitor is <b>running normally</b>.</p>
  <p><b>Nothing new to report in this weekly status email.</b> Daily scans will
  still email you immediately if a campsite matching your filters opens up.</p>
  <p>Monitoring <b>{len(campground_names)} campgrounds</b> with a {lookahead_weeks}-week lookahead:</p>
  <ul>{cg_list_html}</ul>
  <p style="color:#555">Outside this weekly status email, you will only receive emails
  when a site matching your filters opens up (hookup + dump station + 25 ft+
  pull-through + ocean adjacent).</p>
  <p style="color:#999;font-size:12px;border-top:1px solid #eee;padding-top:8px;margin-top:16px">
    Sent at {detected}
  </p>
</div>
</body></html>
""".strip()

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = cfg.alert_from_email
    msg["To"] = cfg.alert_to_email
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.login(cfg.alert_from_email, cfg.alert_from_password)
            server.sendmail(cfg.alert_from_email, cfg.alert_to_email, msg.as_string())
        log.info("Heartbeat email sent.")
    except Exception as exc:
        log.error("Failed to send heartbeat: %s", exc)


def send_alert(slot: AvailableSlot, cfg: Config) -> None:
    msg = _build_email(slot, cfg)
    try:
        with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.login(cfg.alert_from_email, cfg.alert_from_password)
            server.sendmail(cfg.alert_from_email, cfg.alert_to_email, msg.as_string())
        log.info("Alert sent: %s", msg["Subject"])
    except Exception as exc:
        log.error("Failed to send alert for %s: %s", slot.slot_id, exc)
        raise
