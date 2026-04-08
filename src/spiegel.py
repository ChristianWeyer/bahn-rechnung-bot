"""Spiegel Abo-Rechnungs-Download."""

import os
import re
import time
from pathlib import Path

import requests as http_req
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

from src.config import ROOT_DIR, _get_secret

KONTO_URL = "https://gruppenkonto.spiegel.de/meinkonto/uebersicht.html"
BROWSER_DATA = ROOT_DIR / ".browser-data-spiegel"

SPIEGEL_EMAIL = _get_secret("SPIEGEL_EMAIL", "op://Shared/Spiegel/username")
SPIEGEL_PASSWORD = _get_secret("SPIEGEL_PASSWORD", "op://Shared/Spiegel/password")


def _login_spiegel(page, email: str, password: str) -> bool:
    """Login bei Spiegel Gruppenkonto (zweistufig: Email → Passwort)."""
    print("  🔑 Spiegel Login ...")

    # Schritt 1: Email eingeben
    email_input = page.locator('input[name="loginform:username"], input[type="email"]')
    if email_input.count() > 0:
        email_input.first.fill(email)
        page.wait_for_timeout(500)

    # "Anmelden oder Konto erstellen" klicken
    submit = page.locator('button:has-text("Anmelden"), button[type="submit"]')
    if submit.count() > 0:
        submit.first.click()
        page.wait_for_timeout(3000)

    # Schritt 2: Passwort eingeben (erscheint erst nach Email-Submit)
    pw_input = page.locator('input[name="password"]:visible, input[type="password"]:visible')
    try:
        pw_input.first.wait_for(state="visible", timeout=10000)
        pw_input.first.fill(password)
        page.wait_for_timeout(500)

        submit = page.locator('button:has-text("Anmelden"), button[type="submit"]')
        if submit.count() > 0:
            submit.first.click()
            page.wait_for_timeout(5000)
    except PlaywrightTimeout:
        print("  ⚠️  Passwort-Feld nicht sichtbar")
        return False

    if "anmelden" in page.url:
        print("  ❌ Spiegel Login fehlgeschlagen")
        return False

    print("  ✅ Spiegel Login erfolgreich")
    return True


def download_spiegel_invoices(
    entries: list[dict],
    download_dir: Path,
    headed: bool = False,
) -> list[Path]:
    """Lädt Spiegel Abo-Rechnungen herunter."""
    download_dir.mkdir(parents=True, exist_ok=True)

    spiegel_entries = [
        e for e in entries
        if not e.get("is_credit") and "SPIEGEL" in e.get("vendor", "").upper()
    ]
    if not spiegel_entries:
        return []

    if not SPIEGEL_EMAIL or not SPIEGEL_PASSWORD:
        print("\n📰 Spiegel: Keine Credentials konfiguriert")
        return []

    print(f"\n📰 Spiegel: Suche {len(spiegel_entries)} Rechnung(en) ...")

    BROWSER_DATA.mkdir(exist_ok=True)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(BROWSER_DATA),
            headless=not headed,
            accept_downloads=True,
            locale="de-DE",
        )
        page = context.new_page()

        try:
            page.goto(KONTO_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(5000)

            # Login falls nötig
            if "anmelden" in page.url:
                if not _login_spiegel(page, SPIEGEL_EMAIL, SPIEGEL_PASSWORD):
                    return []
                page.goto(KONTO_URL, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(5000)

            # Rechnungen suchen
            text = page.inner_text('body')
            print(f"  ✅ Konto geladen")

            # Nach Rechnungs-Links suchen
            links = page.query_selector_all('a')
            invoice_links = []
            for a in links:
                href = a.get_attribute('href') or ''
                t = (a.text_content() or '').strip()
                if any(k in t.lower() + href.lower() for k in ['rechnung', 'invoice', 'pdf', 'beleg']):
                    invoice_links.append({"text": t, "href": href})

            if invoice_links:
                print(f"  📋 {len(invoice_links)} Rechnungs-Link(s) gefunden")
            else:
                print("  ⚠️  Keine Rechnungs-Links auf der Kontoseite")

            downloaded = []
            cookies = page.context.cookies("https://gruppenkonto.spiegel.de")
            cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)

            for entry in spiegel_entries:
                amount = entry.get("amount", 0)
                date_str = entry.get("date", "")
                print(f"  🔍 Spiegel  {amount:.2f} EUR  ({date_str})")

                # Versuche Rechnungs-Links
                for link in invoice_links:
                    href = link["href"]
                    if not href:
                        continue

                    full_url = href if href.startswith("http") else f"https://gruppenkonto.spiegel.de{href}"
                    try:
                        resp = http_req.get(full_url, headers={"Cookie": cookie_str}, timeout=15)
                        if resp.status_code == 200 and resp.content[:4] == b"%PDF":
                            date_prefix = date_str.replace(".", "") + "_" if date_str else ""
                            fname = f"{date_prefix}Spiegel_Rechnung.pdf"
                            save_path = download_dir / fname
                            save_path.write_bytes(resp.content)
                            downloaded.append(save_path)
                            print(f"  ✅ {fname} ({len(resp.content) / 1024:.1f} KB)")
                            break
                    except Exception as e:
                        print(f"  ⚠️  Download fehlgeschlagen: {e}")

                else:
                    print(f"  ⚠️  Keine Rechnung gefunden")

            if downloaded:
                print(f"  📦 {len(downloaded)} Spiegel-Rechnung(en) heruntergeladen")
            return downloaded

        finally:
            context.close()
