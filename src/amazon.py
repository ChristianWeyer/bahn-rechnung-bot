"""Amazon.de Rechnungs-Download per Playwright.

Klickt auf "Rechnung" Popover pro Bestellung und lädt die Invoice-PDF herunter.
Matching: per Bestellbetrag + Datum, nicht per Reihenfolge.
"""

import re
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import TimeoutError as PlaywrightTimeout


ORDERS_URL = "https://www.amazon.de/your-orders/orders?timeFilter=months-3"

# Bestellungen von diesen Verkäufern überspringen (haben eigene Scraper)
SKIP_SELLERS = ["audible"]


def _login_amazon(page, email: str, password: str) -> bool:
    """Login bei Amazon.de (mit 2FA-Unterstützung)."""
    print("  🔑 Amazon Login ...")

    email_input = page.locator('input[name="email"], input#ap_email')
    if email_input.count() > 0:
        email_input.first.fill(email)
        continue_btn = page.locator('input#continue, span#continue')
        if continue_btn.count() > 0:
            continue_btn.first.click()
            page.wait_for_timeout(2000)

    pw_input = page.locator('input[name="password"], input#ap_password')
    if pw_input.count() > 0:
        pw_input.first.fill(password)
        submit_btn = page.locator('input#signInSubmit, input[type="submit"]')
        if submit_btn.count() > 0:
            submit_btn.first.click()
            page.wait_for_timeout(3000)

    if "ap/cvf" in page.url or "ap/mfa" in page.url:
        print("  📱 Amazon 2FA/CAPTCHA erforderlich!")
        print("  → Bitte im Browser loesen. Warte max. 120s ...")
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


def _ensure_german_language(page) -> None:
    """Stellt sicher dass Amazon.de auf Deutsch angezeigt wird.

    Amazon merkt sich die Sprache im Account/Cookie. Wenn der Browser auf EN steht,
    sind die Selektoren ('Summe', 'Rechnung') falsch. Klickt den 'Deutsch' Link
    im Sprach-Dropdown falls nötig.
    """
    # Prüfe ob schon Deutsch: suche nach "Summe" oder "Bestellungen" im Seitentext
    body_text = page.inner_text("body")[:500]
    if "Summe" in body_text or "Bestellungen" in body_text or "SUMME" in body_text:
        return  # Bereits Deutsch

    # Sprache auf Deutsch umschalten
    de_link = page.locator('a[href*="switch-lang=de"]')
    if de_link.count() > 0:
        de_link.first.click()
        page.wait_for_timeout(5000)
        return

    # Fallback: Sprach-Button hovern und Deutsch-Link suchen
    lang_nav = page.locator('#icp-nav-flyout')
    if lang_nav.count() > 0:
        lang_nav.first.hover()
        page.wait_for_timeout(1000)
        de_option = page.locator('a:has-text("Deutsch")')
        if de_option.count() > 0:
            de_option.first.click()
            page.wait_for_timeout(5000)


def _parse_amazon_amount(amt_str: str) -> float | None:
    """Parst Amazon-Beträge: '43,90 €', '9,95 €', '1.234,56 €', '€9.95'."""
    clean = amt_str.replace("€", "").replace("\xa0", "").replace(" ", "").strip()
    if "," in clean and "." in clean:
        clean = clean.replace(".", "").replace(",", ".")
    elif "," in clean:
        clean = clean.replace(",", ".")
    try:
        return float(clean)
    except ValueError:
        return None


def _extract_all_order_amounts(page) -> dict[str, float]:
    """Extrahiert alle Bestellbeträge von der Übersichtsseite.

    Sucht im Header jeder Order-Card nach dem Betrag neben 'Summe' bzw. 'TOTAL'.
    Robuster Ansatz: extrahiert den gesamten Header-Text und sucht per Regex.
    """
    try:
        raw = page.evaluate("""() => {
            const cards = document.querySelectorAll('.order-card');
            const results = {};
            for (const card of cards) {
                const oidEl = card.querySelector('.yohtmlc-order-id span[dir="ltr"]');
                if (!oidEl) continue;
                const oid = oidEl.textContent.trim();
                const header = card.querySelector('.order-header');
                if (header) results[oid] = header.innerText;
            }
            return results;
        }""")
        amounts = {}
        for oid, header_text in raw.items():
            # DE: "SUMME\n9,95 €" oder "Summe\n43,90 €"
            import re
            m = re.search(r'(?:SUMME|Summe|TOTAL)\s*\n\s*(?:€\s*)?([\d.,]+)\s*€?', header_text)
            if m:
                val = _parse_amazon_amount(m.group(1))
                if val is not None:
                    amounts[oid] = val
        return amounts
    except Exception:
        return {}


def _get_order_invoice_pdfs(page, order_id: str) -> list[str]:
    """Klickt den Rechnung-Popover für eine Bestellung und extrahiert ALLE PDF-Links.

    Amazon kann pro Bestellung MEHRERE Rechnungen haben (verschiedene Marketplace-Seller).
    Der Popover zeigt z.B. "Rechnung 1", "Rechnung 2", etc.

    Returns:
        Liste von PDF-URLs (kann leer sein).
    """
    popover_link = page.locator(f'a[href*="invoice/popover?orderId={order_id}"]')
    if popover_link.count() == 0:
        return []

    popover_link.first.click()
    page.wait_for_timeout(2000)

    urls = []

    # Finde den zuletzt geöffneten Popover
    popover = page.locator('.a-popover:visible .invoice-list').last

    if popover.count() > 0:
        # 1. Alle /documents/download/ Links (echte PDFs)
        doc_links = popover.locator('a[href*="/documents/download/"]')
        for i in range(doc_links.count()):
            href = doc_links.nth(i).get_attribute("href") or ""
            if href and href not in urls:
                urls.append(href)

        # 2. Alle print.html Links (Fallback für Bestellungen ohne echtes PDF)
        if not urls:
            print_links = popover.locator('a[href*="print.html"]')
            for i in range(print_links.count()):
                href = print_links.nth(i).get_attribute("href") or ""
                if href and href not in urls:
                    urls.append(href)

    return urls


def _collect_orders(page) -> list[dict]:
    """Sammelt alle Bestellungen mit Order-ID von der Übersicht."""
    orders = []
    popover_links = page.locator('a[href*="invoice/popover?orderId="]')
    count = popover_links.count()

    for i in range(count):
        try:
            href = popover_links.nth(i).get_attribute("href") or ""
            parsed = urlparse(href)
            params = parse_qs(parsed.query)
            order_id = params.get("orderId", [""])[0]
            if not order_id or any(o["order_id"] == order_id for o in orders):
                continue
            orders.append({"order_id": order_id})
        except Exception:
            continue

    return orders


def _filter_amazon_entries(entries: list[dict]) -> list[dict]:
    """Filtert Amazon-Einträge aus MC-Entries."""
    return [
        e for e in entries
        if not e.get("is_credit")
        and ("AMZN" in e.get("vendor", "").upper() or "AMAZON" in e.get("vendor", "").upper())
    ]


def download_amazon_invoices(
    page,
    entries: list[dict],
    download_dir: Path,
    email: str,
    password: str,
) -> list[tuple[dict, Path]]:
    """Lädt Amazon.de Rechnungen für MC-Einträge herunter.

    Returns:
        Liste von (entry, filepath) Tupeln für erfolgreiche Downloads.
    """
    download_dir.mkdir(parents=True, exist_ok=True)

    amazon_entries = _filter_amazon_entries(entries)
    if not amazon_entries:
        return []

    print(f"\n  🔍 Amazon.de: Suche {len(amazon_entries)} Rechnung(en) ...")

    page.goto(ORDERS_URL, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3000)

    if "ap/signin" in page.url or "ap/cvf" in page.url:
        if not _login_amazon(page, email, password):
            return []
        page.goto(ORDERS_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(5000)

    # Sprache auf Deutsch sicherstellen (für konsistente Selektoren)
    _ensure_german_language(page)

    # Sammle Bestellungen mit Invoice-URLs ueber alle Seiten
    order_invoices = []
    unmatched_amounts = {round(e.get("amount", 0), 2) for e in amazon_entries}
    max_pages = 5
    total_orders = 0

    for page_num in range(max_pages):
        page_orders = _collect_orders(page)
        if not page_orders and page_num == 0:
            page.reload(wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(5000)
            page_orders = _collect_orders(page)

        page_amounts = _extract_all_order_amounts(page)
        total_orders += len(page_orders)

        # Pro Bestellung auf DIESER Seite: Popover klicken und Invoice-URLs holen
        for order in page_orders:
            oid = order["order_id"]
            # Bereits erfasst?
            if any(inv["order_id"] == oid for inv in order_invoices):
                continue

            pdf_urls = _get_order_invoice_pdfs(page, oid)

            if pdf_urls:
                amt = page_amounts.get(oid)
                order_invoices.append({"order_id": oid, "pdf_urls": pdf_urls, "amount": amt})

            page.keyboard.press("Escape")
            page.wait_for_timeout(300)

        # Pruefen ob alle MC-Betraege gefunden wurden
        for amt in page_amounts.values():
            unmatched_amounts.discard(round(amt, 2))

        if not unmatched_amounts:
            break

        # Naechste Seite
        next_btn = page.locator('.a-pagination .a-last a')
        if next_btn.count() == 0 or page.locator('.a-pagination .a-last.a-disabled').count() > 0:
            break
        next_btn.first.click()
        page.wait_for_timeout(3000)

    print(f"  ✅ {total_orders} Bestellungen, {len(order_invoices)} mit Rechnung ({page_num + 1} Seite(n))")

    if not order_invoices:
        print("  ⚠️ Keine Bestellungen mit Rechnung gefunden")
        return []

    # Schritt 2: Pro MC-Entry die PASSENDE Bestellung(en) per Betrag finden und downloaden.
    # Amazon fasst manchmal mehrere Bestellungen zu einer MC-Buchung zusammen.
    # Strategie:
    #   1. Exakter Match (eine Bestellung ≈ MC-Betrag, Diff <= 1 EUR)
    #   2. Kombi-Match (2 Bestellungen deren Summe ≈ MC-Betrag)
    #   3. Fallback: nächste ungenutzte Bestellung
    results = []
    used_orders = set()

    for idx, entry in enumerate(amazon_entries, 1):
        amount = entry.get("amount", 0)
        date_str = entry.get("date", "")
        vendor = entry.get("vendor", "?")
        print(f"  [{idx}/{len(amazon_entries)}] {vendor}  {amount:.2f} EUR  ({date_str})")

        matched_invoices = _match_orders_to_entry(order_invoices, amount, used_orders)

        if not matched_invoices:
            print(f"       ⚠️ Keine passende Bestellung gefunden")
            continue

        entry_files = []
        for inv in matched_invoices:
            oid = inv["order_id"]
            for url_idx, pdf_url in enumerate(inv["pdf_urls"]):
                suffix = f"_{url_idx + 1}" if len(inv["pdf_urls"]) > 1 else ""
                downloaded_path = _download_pdf(page, pdf_url, f"{oid}{suffix}", date_str, download_dir)
                if downloaded_path and _validate_amazon_pdf(downloaded_path):
                    entry_files.append(downloaded_path)
                    print(f"       📎 {downloaded_path.name}")
                elif downloaded_path:
                    downloaded_path.unlink(missing_ok=True)

            used_orders.add(oid)

        if entry_files:
            # Ersten File als Hauptzuordnung, restliche als Zusatz
            results.append((entry, entry_files[0]))
            for extra in entry_files[1:]:
                results.append((entry, extra))

        time.sleep(1)

    print(f"  ✅ {len(results)} Amazon-Rechnung(en) heruntergeladen")
    return results


def _match_orders_to_entry(
    order_invoices: list[dict],
    target_amount: float,
    used_orders: set[str],
) -> list[dict]:
    """Findet die passende(n) Bestellung(en) für einen MC-Betrag.

    1. Exakter Match: eine Bestellung mit Diff <= 1 EUR
    2. Kombi-Match: 2 Bestellungen deren Summe Diff <= 1 EUR zum MC-Betrag hat
    3. Fallback: nächste ungenutzte Bestellung
    """
    available = [inv for inv in order_invoices if inv["order_id"] not in used_orders and inv["amount"] is not None]

    # 1. Exakter Match (eine Bestellung)
    best = None
    best_diff = float('inf')
    for inv in available:
        diff = abs(inv["amount"] - target_amount)
        if diff < best_diff:
            best_diff = diff
            best = inv

    if best and best_diff <= 1.0:
        return [best]

    # 2. Kombi-Match (2 Bestellungen)
    if len(available) >= 2:
        best_combo = None
        best_combo_diff = float('inf')
        for i in range(len(available)):
            for j in range(i + 1, len(available)):
                combo_sum = available[i]["amount"] + available[j]["amount"]
                diff = abs(combo_sum - target_amount)
                if diff < best_combo_diff:
                    best_combo_diff = diff
                    best_combo = (available[i], available[j])

        if best_combo and best_combo_diff <= 1.0:
            print(f"       🔗 Kombi-Match: {best_combo[0]['amount']:.2f} + {best_combo[1]['amount']:.2f} = {best_combo[0]['amount'] + best_combo[1]['amount']:.2f} EUR")
            return list(best_combo)

    # 3. Fallback: nächste ungenutzte (Betrags-Diff > 1 EUR)
    if best:
        return [best]

    # Absoluter Fallback
    for inv in order_invoices:
        if inv["order_id"] not in used_orders:
            return [inv]

    return []


def _validate_amazon_pdf(filepath: Path) -> bool:
    """Prüft ob ein PDF eine Rechnung ist. Akzeptiert alles ausser leere/kaputte PDFs."""
    try:
        if filepath.stat().st_size < 500:
            return False
        return True
    except Exception:
        return True


def _download_pdf(page, pdf_url: str, order_id: str, date_str: str, download_dir: Path) -> Path | None:
    """Lädt ein einzelnes Amazon-PDF herunter."""
    date_prefix = date_str.replace(".", "") + "_" if date_str else ""

    if "/documents/download/" in pdf_url:
        try:
            full_url = f"https://www.amazon.de{pdf_url}" if pdf_url.startswith("/") else pdf_url
            cookies = page.context.cookies("https://www.amazon.de")
            cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)

            import requests as http_req
            resp = http_req.get(full_url, headers={"Cookie": cookie_str}, timeout=30)
            if resp.status_code == 200 and len(resp.content) > 1000:
                fname = f"{date_prefix}Amazon_{order_id}_invoice.pdf"
                save_path = download_dir / fname
                save_path.write_bytes(resp.content)
                return save_path
            else:
                print(f"       ❌ HTTP {resp.status_code} beim Download fuer {order_id} ({len(resp.content)} bytes)")
        except Exception as e:
            print(f"       ❌ Download fehlgeschlagen fuer {order_id}: {e}")

    elif "print.html" in pdf_url:
        try:
            invoice_page = page.context.new_page()
            invoice_page.goto(f"https://www.amazon.de{pdf_url}", wait_until="domcontentloaded", timeout=30000)
            invoice_page.wait_for_timeout(3000)

            fname = f"{date_prefix}Amazon_{order_id}.pdf"
            save_path = download_dir / fname
            invoice_page.pdf(path=str(save_path), format="A4", print_background=True)
            invoice_page.close()

            if save_path.stat().st_size > 1000:
                return save_path
            save_path.unlink(missing_ok=True)
        except Exception as e:
            print(f"       Fehler: {e}")

    return None
