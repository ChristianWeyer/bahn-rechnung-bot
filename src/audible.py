"""Audible.de Monatsbeitrags-Rechnungen per Playwright."""

import re
import time
from pathlib import Path

import requests as http_req
from playwright.sync_api import TimeoutError as PlaywrightTimeout


MEMBERSHIP_URL = "https://www.audible.de/account/purchase-history?tf=membership&df=last_365_days&ps=20"


def download_audible_invoices(
    page,
    entries: list[dict],
    download_dir: Path,
) -> list[Path]:
    """Lädt Audible-Monatsbeitrags-Rechnungen."""
    download_dir.mkdir(parents=True, exist_ok=True)

    audible_entries = [
        e for e in entries
        if not e.get("is_credit") and "AUDIBLE" in e.get("vendor", "").upper()
    ]
    if not audible_entries:
        return []

    print(f"\n🎧 Audible: Suche {len(audible_entries)} Rechnung(en) ...")

    page.goto(MEMBERSHIP_URL, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(5000)

    if "signin" in page.url or "ap/signin" in page.url:
        print("  ❌ Nicht bei Audible eingeloggt")
        return []

    # Alle Order-Details-Links sammeln
    detail_links = page.locator('a[href*="order-details"]')
    count = detail_links.count()
    print(f"  📋 {count} Monatsbeitrag(e) gefunden")

    if count == 0:
        return []

    # Cookies für HTTP-Downloads
    cookies = page.context.cookies("https://www.audible.de")
    cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)

    downloaded = []

    for entry in audible_entries:
        amount = entry.get("amount", 0)
        date_str = entry.get("date", "")
        print(f"  🔍 Audible  {amount:.2f} EUR  ({date_str})")

        # Jede Order-Details-Seite öffnen und Rechnung suchen
        for i in range(count):
            href = detail_links.nth(i).get_attribute("href") or ""
            if not href or href in [d.get("_href") for d in downloaded]:
                continue

            full_url = f"https://www.audible.de{href}" if href.startswith("/") else href
            detail_page = page.context.new_page()

            try:
                detail_page.goto(full_url, wait_until="domcontentloaded", timeout=15000)
                detail_page.wait_for_timeout(3000)

                # Prüfe ob der Betrag passt
                page_text = detail_page.inner_text('body')
                amount_str = f"{amount:.2f}".replace(".", ",")
                if amount_str not in page_text:
                    detail_page.close()
                    continue

                # Rechnung-Link finden
                invoice_link = detail_page.locator('a[href*="/documents/download/"][href*="Invoice"]')
                if invoice_link.count() == 0:
                    invoice_link = detail_page.locator('a:has-text("Rechnung")')

                if invoice_link.count() > 0:
                    inv_href = invoice_link.first.get_attribute("href") or ""
                    if inv_href:
                        pdf_url = f"https://www.audible.de{inv_href}" if inv_href.startswith("/") else inv_href

                        resp = http_req.get(pdf_url, headers={"Cookie": cookie_str}, timeout=15)
                        if resp.status_code == 200 and resp.content[:4] == b"%PDF":
                            date_prefix = date_str.replace(".", "") + "_" if date_str else ""
                            fname = f"{date_prefix}Audible_Rechnung.pdf"
                            save_path = download_dir / fname
                            save_path.write_bytes(resp.content)
                            downloaded.append({"path": save_path, "_href": href})
                            print(f"  ✅ {fname} ({len(resp.content) / 1024:.1f} KB)")
                            detail_page.close()
                            break
            except Exception as e:
                print(f"  ⚠️  Fehler: {e}")
            finally:
                if not detail_page.is_closed():
                    detail_page.close()
        else:
            print(f"  ⚠️  Keine passende Rechnung gefunden")

        time.sleep(0.5)

    result = [d["path"] for d in downloaded]
    if result:
        print(f"  📦 {len(result)} Audible-Rechnung(en) heruntergeladen")
    return result
