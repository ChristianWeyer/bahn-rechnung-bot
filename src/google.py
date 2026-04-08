"""Google Payments Beleg-Download (YouTube Premium, Google One).

Transaktionen sind in einem iframe von payments.google.com.
Nutzt CDP weil Google den Content nur im echten Chrome rendert.
"""

import re
import time
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeout


ACTIVITY_URL = "https://pay.google.com/gp/w/home/activity"


def download_google_invoices(
    page,
    entries: list[dict],
    download_dir: Path,
) -> list[Path]:
    """Lädt Google Payment Belege über CDP (iframe-basiert)."""
    download_dir.mkdir(parents=True, exist_ok=True)

    google_entries = [
        e for e in entries
        if not e.get("is_credit")
        and any(k in e.get("vendor", "").upper() for k in ["GOOGLE", "YOUTUBE", "WL*GOOGLE"])
    ]
    if not google_entries:
        return []

    print(f"\n🔍 Google Payments: Suche {len(google_entries)} Beleg(e) ...")

    page.goto(ACTIVITY_URL, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(10000)

    # Transaktionen sind im iframe von payments.google.com
    iframe = None
    for frame in page.frames:
        if "payments.google.com" in frame.url and "timelineview" in frame.url:
            iframe = frame
            break

    if not iframe:
        print("  ⚠️  Kein payments.google.com iframe gefunden")
        return []

    # Prüfe ob Transaktionen sichtbar sind
    text = iframe.evaluate("() => document.body ? document.body.innerText : ''")
    if 'YouTube' not in text and '€' not in text:
        print("  ❌ Keine Transaktionen im iframe")
        return []

    print("  ✅ Transaktionen geladen")

    # Cookies für HTTP-Downloads
    cookies = page.context.cookies("https://payments.google.com")
    cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)

    downloaded = []

    for entry in google_entries:
        amount = entry.get("amount", 0)
        date_str = entry.get("date", "")
        vendor = entry.get("vendor", "?")
        amount_str = f"{amount:.2f}".replace(".", ",")

        print(f"  🔍 {vendor}  {amount:.2f} EUR  ({date_str})")

        # Klick auf Transaktion im iframe um Detail-Panel zu öffnen
        found = iframe.evaluate(f"""() => {{
            const items = document.querySelectorAll('[data-was-visible="true"]');
            for (const item of items) {{
                const text = item.textContent || '';
                if ((text.includes('{amount_str}') || text.includes('−{amount_str}'))
                    && text.includes('€')) {{
                    item.click();
                    return true;
                }}
            }}
            return false;
        }}""")

        if not found:
            print(f"  ⚠️  Betrag {amount_str} € nicht gefunden")
            continue

        # Warten bis Detail-Panel rendert und Invoice-URL in neuem Frame erscheint
        page.wait_for_timeout(5000)

        # Die Invoice-URL ist nicht per CDP auslesbar (Google Content-Protection).
        # Wir können das Detail-Panel nicht automatisch scrapen.
        print(f"  ⚠️  Detail-Panel nicht per CDP auslesbar")

        # Zurück navigieren und iframe neu finden
        page.goto(ACTIVITY_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(8000)
        iframe = None
        for frame in page.frames:
            if "payments.google.com" in frame.url and "timelineview" in frame.url:
                iframe = frame
                break
        if not iframe:
            break

        time.sleep(1)

    if downloaded:
        print(f"  📦 {len(downloaded)} Google-Beleg(e) heruntergeladen")
    return downloaded
