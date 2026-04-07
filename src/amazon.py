"""
Amazon.de Rechnungs-Download
=============================
Lädt Rechnungen von Amazon.de per Playwright herunter.
Matcht MC-Einträge anhand von Betrag und Datum gegen die Bestellübersicht.

Nutzung:
    from fetch_amazon import download_amazon_invoices
    files = download_amazon_invoices(page, entries, download_dir)
"""

import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import TimeoutError as PlaywrightTimeout


ORDERS_URL = "https://www.amazon.de/your-orders/orders?timeFilter=months-3"


def _parse_date(date_str: str) -> datetime | None:
    try:
        return datetime.strptime(date_str, "%d.%m.%y")
    except (ValueError, TypeError):
        return None


def _login_amazon(page, email: str, password: str):
    """Login bei Amazon.de (mit 2FA-Unterstützung)."""
    print("  🔑 Amazon Login ...")

    # Email eingeben
    email_input = page.locator('input[name="email"], input#ap_email')
    if email_input.count() > 0:
        email_input.first.fill(email)
        continue_btn = page.locator('input#continue, span#continue')
        if continue_btn.count() > 0:
            continue_btn.first.click()
            page.wait_for_timeout(2000)

    # Passwort eingeben
    pw_input = page.locator('input[name="password"], input#ap_password')
    if pw_input.count() > 0:
        pw_input.first.fill(password)
        submit_btn = page.locator('input#signInSubmit, input[type="submit"]')
        if submit_btn.count() > 0:
            submit_btn.first.click()
            page.wait_for_timeout(3000)

    # 2FA / CAPTCHA erkennen
    if "ap/cvf" in page.url or "ap/mfa" in page.url:
        print("  📱 Amazon 2FA/CAPTCHA erforderlich!")
        print("  → Bitte im Browser lösen. Warte max. 120s ...")
        try:
            page.wait_for_url(
                lambda u: "your-orders" in u or "gp/css" in u or "amazon.de/?ref" in u,
                timeout=120000,
            )
        except PlaywrightTimeout:
            print("  ❌ Amazon Login Timeout")
            return False

    if "ap/signin" in page.url:
        print("  ❌ Amazon Login fehlgeschlagen")
        return False

    print("  ✅ Amazon Login erfolgreich")
    return True


def _extract_invoice_links(page) -> list[dict]:
    """Extrahiert alle Rechnungs-Links und zugehörige Bestelldaten von der Bestellübersicht."""
    orders = []

    # Alle Invoice-Links finden
    invoice_links = page.locator('a[href*="/gp/css/summary/print.html"], a[href*="invoice"]')
    count = invoice_links.count()

    if count == 0:
        # Alternative: nach "Rechnung" Text suchen
        invoice_links = page.locator('a:has-text("Rechnung")')
        count = invoice_links.count()

    for i in range(count):
        try:
            link = invoice_links.nth(i)
            href = link.evaluate("el => el.href") or ""
            if "/gp/css/summary/print.html" not in href:
                continue

            # Order-ID aus URL extrahieren
            parsed = urlparse(href)
            params = parse_qs(parsed.query)
            order_id = params.get("orderID", [""])[0]
            if not order_id:
                continue

            orders.append({
                "order_id": order_id,
                "invoice_url": href,
            })
        except Exception:
            continue

    return orders


def _find_order_amounts(page) -> dict[str, float]:
    """Extrahiert Bestellbeträge von der Bestellübersicht (Order-ID → Betrag)."""
    amounts = {}

    # Amazon zeigt Beträge in der Bestellübersicht
    result = page.evaluate("""() => {
        const orders = {};
        // Suche nach Order-Blöcken
        const orderCards = document.querySelectorAll('.order, [class*="order-card"], .a-box-group');
        for (const card of orderCards) {
            const text = card.innerText || '';
            // Order-ID finden
            const idMatch = text.match(/(\\d{3}-\\d{7}-\\d{7})/);
            if (!idMatch) continue;
            // Betrag finden (EUR Format: 1.234,56 oder 123,45)
            const amountMatch = text.match(/EUR\\s*([\\d.,]+)|([\\d.,]+)\\s*€/);
            if (amountMatch) {
                const amountStr = (amountMatch[1] || amountMatch[2])
                    .replace('.', '').replace(',', '.');
                const amount = parseFloat(amountStr);
                if (amount > 0) {
                    orders[idMatch[1]] = amount;
                }
            }
        }
        return orders;
    }""")

    if isinstance(result, dict):
        amounts = {k: float(v) for k, v in result.items()}

    return amounts


def download_amazon_invoices(
    page,
    entries: list[dict],
    download_dir: Path,
    email: str,
    password: str,
) -> list[Path]:
    """
    Loggt sich bei Amazon.de ein und lädt Rechnungen für die
    MC-Einträge herunter (basierend auf Betrag).
    """
    download_dir.mkdir(parents=True, exist_ok=True)

    # Nur Amazon-Einträge (Belastungen)
    amazon_entries = [
        e for e in entries
        if not e.get("is_credit")
        and ("AMZN" in e.get("vendor", "").upper() or "AMAZON" in e.get("vendor", "").upper())
    ]
    if not amazon_entries:
        return []

    print(f"\n🛒 Amazon.de: Suche {len(amazon_entries)} Rechnung(en) ...")

    # Bestellübersicht laden
    page.goto(ORDERS_URL, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3000)

    # Login falls nötig
    if "ap/signin" in page.url or "ap/cvf" in page.url:
        if not _login_amazon(page, email, password):
            return []
        page.goto(ORDERS_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3000)

    # Invoice-Links und Beträge sammeln
    invoice_links = _extract_invoice_links(page)
    order_amounts = _find_order_amounts(page)

    print(f"  📋 {len(invoice_links)} Bestellungen mit Rechnungs-Link gefunden")

    if not invoice_links:
        print("  ⚠️  Keine Rechnungs-Links auf der Bestellübersicht")
        return []

    # MC-Einträge gegen Bestellungen matchen (nach Betrag)
    downloaded = []
    matched_order_ids = set()

    for entry in amazon_entries:
        amount = entry.get("amount", 0)
        date_str = entry.get("date", "")
        vendor = entry.get("vendor", "?")
        print(f"  🔍 {vendor}  {amount:.2f} EUR  ({date_str})")

        # Match nach Betrag (±1 EUR Toleranz für Rundungen)
        best_match = None
        for order in invoice_links:
            oid = order["order_id"]
            if oid in matched_order_ids:
                continue
            order_amount = order_amounts.get(oid, 0)
            if abs(order_amount - amount) <= 1.0:
                best_match = order
                break

        if not best_match:
            # Fallback: einfach alle ungematchten Invoices der Reihe nach zuordnen
            for order in invoice_links:
                if order["order_id"] not in matched_order_ids:
                    best_match = order
                    break

        if not best_match:
            print(f"       ⚠️  Keine passende Bestellung gefunden")
            continue

        oid = best_match["order_id"]
        matched_order_ids.add(oid)
        print(f"       → Bestellung {oid}")

        # Rechnung herunterladen
        try:
            invoice_url = best_match["invoice_url"]
            # Neue Seite für die Rechnung öffnen
            invoice_page = page.context.new_page()
            invoice_page.goto(invoice_url, wait_until="domcontentloaded", timeout=30000)
            invoice_page.wait_for_timeout(3000)

            # Seite als PDF speichern
            date_prefix = date_str.replace(".", "") if date_str else ""
            fname = f"{date_prefix}_Amazon_{oid}.pdf"
            save_path = download_dir / fname
            invoice_page.pdf(path=str(save_path), format="A4", print_background=True)
            invoice_page.close()

            if save_path.stat().st_size > 1000:
                downloaded.append(save_path)
                print(f"       ✅ {fname} ({save_path.stat().st_size / 1024:.1f} KB)")
            else:
                save_path.unlink(missing_ok=True)
                print(f"       ⚠️  PDF zu klein — vermutlich Fehler")
        except Exception as e:
            print(f"       ⚠️  Download fehlgeschlagen: {e}")

        time.sleep(1)  # Rate-Limiting

    print(f"  📦 {len(downloaded)} Amazon-Rechnung(en) heruntergeladen")
    return downloaded
