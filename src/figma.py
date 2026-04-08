"""Figma Rechnungs-Download — Admin Console Billing/Invoices."""

import time
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeout

from src.config import FIGMA_TEAM_ID

FIGMA_INVOICES_URL = f"https://www.figma.com/files/team/{FIGMA_TEAM_ID}/team-admin-console/billing/invoices" if FIGMA_TEAM_ID else ""


def download_figma_invoices(
    page,
    entries: list[dict],
    download_dir: Path,
) -> list[Path]:
    """Lädt Figma-Invoices über die Admin Console."""
    download_dir.mkdir(parents=True, exist_ok=True)

    figma_entries = [
        e for e in entries
        if not e.get("is_credit") and "FIGMA" in e.get("vendor", "").upper()
    ]
    if not figma_entries:
        return []

    if not FIGMA_INVOICES_URL:
        print("\n🎨 Figma: FIGMA_TEAM_ID nicht konfiguriert")
        return []

    print(f"\n🎨 Figma: Suche {len(figma_entries)} Rechnung(en) ...")

    page.goto(FIGMA_INVOICES_URL, wait_until="domcontentloaded", timeout=30000)

    # Warten bis die Invoice-Tabelle geladen ist (SPA — dauert lange!)
    print("  ⏳ Warte auf Invoice-Tabelle (kann bis zu 60s dauern) ...")
    try:
        page.wait_for_function(
            "() => document.querySelectorAll('a').length > 5 && !document.body.innerText.includes('Loading')",
            timeout=60000,
        )
    except PlaywrightTimeout:
        pass
    page.wait_for_timeout(3000)

    # "View invoice" finden — Figma nutzt DIVs statt Links, mit Table-Header Overlay
    # Daher: per JS die Invoice-Rows finden und klicken
    invoice_rows = page.evaluate("""() => {
        const rows = document.querySelectorAll('[role="row"]');
        const results = [];
        for (const row of rows) {
            const text = row.textContent || '';
            if (text.includes('Paid') && text.includes('View invoice')) {
                results.push(true);
            }
        }
        return results.length;
    }""")

    print(f"  📋 {invoice_rows} bezahlte Invoice(s) gefunden")

    if invoice_rows == 0:
        print("  ⚠️  Keine Invoices gefunden")
        return []

    downloaded = []

    for idx, entry in enumerate(figma_entries):
        date_str = entry.get("date", "")
        amount = entry.get("amount", 0)
        print(f"  🔍 Figma  {amount:.2f} EUR  ({date_str})")

        if idx >= invoice_rows:
            print(f"  ⚠️  Nicht genug Invoices auf der Seite")
            break

        try:
            # Invoice-Row per JS klicken (Overlay umgehen)
            page.evaluate(f"""() => {{
                const rows = document.querySelectorAll('[role="row"]');
                let paidIdx = 0;
                for (const row of rows) {{
                    const text = row.textContent || '';
                    if (text.includes('Paid') && text.includes('View invoice')) {{
                        if (paidIdx === {idx}) {{
                            row.click();
                            return;
                        }}
                        paidIdx++;
                    }}
                }}
            }}""")
            page.wait_for_timeout(5000)

            # Stripe-Tab finden
            stripe_page = None
            for p in page.context.pages:
                if "stripe.com" in p.url and p != page:
                    stripe_page = p
                    break

            if not stripe_page:
                # Vielleicht im gleichen Tab
                if "stripe.com" in page.url:
                    stripe_page = page

            if stripe_page:
                stripe_page.wait_for_timeout(5000)
                dl_btn = stripe_page.locator(
                    'a:has-text("Download invoice"), button:has-text("Download invoice")'
                )
                if dl_btn.count() > 0:
                    with stripe_page.expect_download(timeout=15000) as dl_info:
                        dl_btn.first.click()
                    download = dl_info.value
                    fname = download.suggested_filename or f"Figma_invoice.pdf"
                    date_prefix = date_str.replace(".", "") + "_" if date_str else ""
                    save_path = download_dir / f"{date_prefix}{fname}"
                    download.save_as(str(save_path))
                    downloaded.append(save_path)
                    print(f"  ✅ {save_path.name} ({save_path.stat().st_size / 1024:.1f} KB)")

                if stripe_page != page:
                    stripe_page.close()
            else:
                print(f"  ⚠️  Kein Stripe-Tab geöffnet")
        except Exception as e:
            print(f"  ⚠️  Fehler: {e}")

        time.sleep(1)

    if downloaded:
        print(f"  📦 {len(downloaded)} Figma-Rechnung(en) heruntergeladen")
    return downloaded
