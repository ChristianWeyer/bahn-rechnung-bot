"""Email-Versand über Microsoft Graph API."""

import base64
import sys
from datetime import datetime
from pathlib import Path

import requests

from src.config import RECIPIENT_EMAIL, GRAPH_SEND_URL, DOWNLOAD_DIR
from src.auth import get_graph_token
from src.timer import Timer


def send_email(files: list[Path], timer: Timer, dry_run: bool = False, cc_email: str | None = None,
               mc_pdf_name: str | None = None, failed_refs: list[str] | None = None,
               total_refs: int | None = None, unmatched_entries: list[dict] | None = None,
               link_only_entries: list[dict] | None = None):
    """Versendet die Rechnungen und Belege per Microsoft Graph API (OAuth)."""
    if not files:
        print("\n📭 Keine neuen Rechnungen/Belege zum Versenden.")
        return

    cc_info = f" (CC: {cc_email})" if cc_email else ""
    print(f"\n📧 Versende {len(files)} PDF(s) an {RECIPIENT_EMAIL}{cc_info} ...")

    if dry_run:
        print("  🏃 Dry-Run: Email wird NICHT gesendet")
        for f in files:
            print(f"    📎 {f.name} ({f.stat().st_size / 1024:.1f} KB)")
        return

    token = get_graph_token()

    now = datetime.now()
    db_files = [f for f in files if "DB_Rechnung" in f.name]
    receipt_files = [f for f in files if "DB_Rechnung" not in f.name]

    source_line = f"Quelle: Mastercard-Abrechnung \"{mc_pdf_name}\"\n\n" if mc_pdf_name else ""

    db_section = ""
    if db_files:
        if failed_refs and total_refs:
            db_section = (
                f"DB-Rechnungen: {len(db_files)} von {total_refs} heruntergeladen\n"
                f"⚠️  Fehlgeschlagene Buchungen (bitte manuell prüfen):\n"
            )
            for ref in failed_refs:
                db_section += f"  - Auftrag {ref}: https://www.bahn.de/buchung/reise?auftragsnummer={ref}\n"
        elif total_refs:
            db_section = f"DB-Rechnungen: alle {total_refs} erfolgreich heruntergeladen\n"
        db_section += "".join(f"  - {f.name}\n" for f in db_files)
        db_section += "\n"

    receipt_section = ""
    if receipt_files:
        receipt_section = f"Belege aus Outlook: {len(receipt_files)} PDFs heruntergeladen\n"
        receipt_section += "".join(f"  - {f.name}\n" for f in receipt_files)
        receipt_section += "\n"

    link_section = ""
    if link_only_entries:
        link_section = f"ℹ️  Belege ohne PDF-Anhang ({len(link_only_entries)}) – bitte manuell herunterladen:\n"
        for m in link_only_entries:
            e = m.get("entry", {})
            vendor = e.get("vendor", "?")
            amount = e.get("amount", 0)
            url = m.get("receipt_url", "")
            link_section += f"  - {vendor}  {amount:.2f} EUR\n"
            if url:
                link_section += f"    → {url}\n"
        link_section += "\n"

    unmatched_section = ""
    if unmatched_entries:
        unmatched_section = f"⚠️  Kein Beleg gefunden für {len(unmatched_entries)} Einträge:\n"
        for e in unmatched_entries:
            vendor = e.get("vendor", "?")
            amount = e.get("amount", 0)
            date = e.get("date", "")
            unmatched_section += f"  - {date}  {vendor}  {amount:.2f} EUR\n"
        unmatched_section += "\n"

    body_text = (
        f"--- Automatisch generierte Email (Expense Bot) ---\n\n"
        f"Hallo,\n\n"
        f"anbei {len(files)} Beleg(e) als PDF im Anhang.\n\n"
        f"{source_line}"
        f"{db_section}"
        f"{receipt_section}"
        f"{link_section}"
        f"{unmatched_section}"
        f"Diese Email wurde automatisch erstellt am {now.strftime('%d.%m.%Y um %H:%M Uhr')}.\n\n"
        f"Bei Fragen bitte direkt an den Absender wenden.\n"
        f"--- Ende der automatischen Nachricht ---"
    )

    attachments = []
    for filepath in files:
        content_bytes = filepath.read_bytes()
        attachments.append({
            "@odata.type": "#microsoft.graph.fileAttachment",
            "name": filepath.name,
            "contentType": "application/pdf",
            "contentBytes": base64.b64encode(content_bytes).decode("utf-8"),
        })

    to_recipients = [{"emailAddress": {"address": RECIPIENT_EMAIL}}]
    cc_recipients = [{"emailAddress": {"address": cc_email}}] if cc_email else []

    has_issues = bool(failed_refs or unmatched_entries)
    subject = (
        f"[Automatisch] Belege ({len(files)} PDFs)"
        f" – {now.strftime('%d.%m.%Y')}"
        f"{f' – {mc_pdf_name}' if mc_pdf_name else ''}"
        f"{' ⚠️ UNVOLLSTÄNDIG' if has_issues else ''}"
    )

    payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": "Text", "content": body_text},
            "toRecipients": to_recipients,
            "ccRecipients": cc_recipients,
            "attachments": attachments,
        },
        "saveToSentItems": True,
    }

    response = requests.post(
        GRAPH_SEND_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30,
    )

    if response.status_code == 202:
        print("  ✅ Email erfolgreich gesendet!")
        print(f"\n  📎 Versendete Rechnungen ({len(files)}):")
        for f in files:
            print(f"     • {f.name} ({f.stat().st_size / 1024:.1f} KB)")
        timer.lap("Email-Versand")
    else:
        print(f"  ❌ Email-Versand fehlgeschlagen (HTTP {response.status_code})")
        print(f"     {response.text}")
        print("     Rechnungen sind trotzdem gespeichert in:", DOWNLOAD_DIR)
        sys.exit(1)
