"""
Beleg-Suche in Outlook
======================
Durchsucht einen Outlook-Mailordner ("Belege") per Microsoft Graph API
nach Rechnungs-Emails, die zu Mastercard-Abrechnungsposten passen,
und lädt die PDF-Anhänge herunter.

Nutzung:
    from fetch_receipts import match_and_download_receipts
    results = match_and_download_receipts(token, entries, download_dir)
"""

import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
DATE_TOLERANCE = int(os.environ.get("BELEGE_DATE_TOLERANCE", "7"))
BELEGE_FOLDER = os.environ.get("BELEGE_FOLDER", "Belege")


# ─── Graph API Helpers ──────────────────────────────────────────────

def _graph_get(url: str, token: str, params: dict | None = None) -> dict:
    """GET-Request an die Graph API."""
    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        params=params,
        timeout=30,
    )
    if resp.status_code == 401:
        print("  ❌ Graph API: Token abgelaufen. Bitte .token_cache.json löschen und neu anmelden.")
        return {}
    if resp.status_code == 403:
        print("  ❌ Graph API: Fehlende Berechtigung (Mail.Read). Bitte .token_cache.json löschen und neu anmelden.")
        return {}
    if resp.status_code != 200:
        print(f"  ⚠️  Graph API Fehler {resp.status_code}: {resp.text[:200]}")
        return {}
    return resp.json()


def find_mail_folder(token: str, folder_name: str = BELEGE_FOLDER) -> str | None:
    """Sucht einen Mailordner per Name. Gibt die Folder-ID zurück."""
    # Top-Level-Ordner durchsuchen
    data = _graph_get(
        f"{GRAPH_BASE}/me/mailFolders",
        token,
        {"$filter": f"displayName eq '{folder_name}'", "$select": "id,displayName"},
    )
    folders = data.get("value", [])
    if folders:
        return folders[0]["id"]

    # In allen Top-Level-Ordnern nach Unterordnern suchen
    all_folders = _graph_get(
        f"{GRAPH_BASE}/me/mailFolders",
        token,
        {"$select": "id,displayName", "$top": "50"},
    )
    for folder in all_folders.get("value", []):
        children = _graph_get(
            f"{GRAPH_BASE}/me/mailFolders/{folder['id']}/childFolders",
            token,
            {"$filter": f"displayName eq '{folder_name}'", "$select": "id,displayName"},
        )
        for child in children.get("value", []):
            return child["id"]

    return None


# ─── Vendor-Matching ────────────────────────────────────────────────

# Mapping von MC-Vendor-Namen zu Suchbegriffen für die Mailsuche
VENDOR_KEYWORDS = {
    "ANTHROPIC": ["anthropic", "claude"],
    "OPENAI": ["openai", "chatgpt"],
    "GITHUB": ["github"],
    "FIGMA": ["figma"],
    "MICROSOFT": ["microsoft", "msbill"],
    "MSFT": ["microsoft", "msbill"],
    "GOOGLE": ["google"],
    "ADOBE": ["adobe"],
    "AMAZON": ["amazon"],
    "AMZN": ["amazon"],
    "HETZNER": ["hetzner"],
    "CLOUDFLARE": ["cloudflare"],
    "RENDER.COM": ["render"],
    "AUTH0": ["auth0"],
    "TAILSCALE": ["tailscale"],
    "PADDLE.NET": ["paddle"],
    "NGROK": ["ngrok"],
    "HUGGINGFACE": ["huggingface", "hugging face"],
    "LANGCHAIN": ["langchain", "langsmith"],
    "LANGFUSE": ["langfuse"],
    "SPIEGEL": ["spiegel"],
    "HANDELSBL": ["handelsblatt"],
    "HEISE": ["heise"],
    "ELEVENLABS": ["elevenlabs"],
    "WINDSURF": ["windsurf"],
    "PERPLEXITY": ["perplexity"],
    "CLAUDE.AI": ["claude"],
}


def _get_search_keywords(vendor: str) -> list[str]:
    """Extrahiert Suchbegriffe aus einem Vendor-Namen."""
    vendor_upper = vendor.upper()
    for prefix, keywords in VENDOR_KEYWORDS.items():
        if prefix in vendor_upper:
            return keywords

    # Fallback: erster sinnvoller Begriff aus dem Vendor-Namen
    # Strip typische Suffixe
    clean = re.sub(r"\s*(GmbH|AG|Ltd|Inc\.|LLC|INC|S\.R\.O\.|SAN FRANCISCO|BERLIN|DUBLIN|NEW YORK|LONDON|LISBOA|LUXEMBOURG|AMSTERDAM|BROOKLYN|SINGAPORE|BASTROP|MOUNTAIN VIEW|PRAGUE|GUNZENHAUSEN|KARLSRUHE).*", "", vendor, flags=re.IGNORECASE)
    clean = re.sub(r"[*#].*", "", clean).strip()
    if clean and len(clean) >= 3:
        return [clean.lower()]
    return [vendor.split(",")[0].split("*")[0].strip().lower()]


def _parse_date(date_str: str) -> datetime | None:
    """Parst ein Datum im Format DD.MM.YY."""
    try:
        return datetime.strptime(date_str, "%d.%m.%y")
    except (ValueError, TypeError):
        return None


# ─── Mail-Suche und Download ────────────────────────────────────────

def search_receipts_for_entry(token: str, folder_id: str, entry: dict) -> list[dict]:
    """Sucht passende Emails zu einem MC-Eintrag im Belege-Ordner."""
    keywords = _get_search_keywords(entry.get("vendor", ""))
    date = _parse_date(entry.get("date", ""))

    if not date:
        return []

    # Zeitfenster: Belegdatum - 3 Tage bis + DATE_TOLERANCE Tage
    date_from = (date - timedelta(days=3)).strftime("%Y-%m-%dT00:00:00Z")
    date_to = (date + timedelta(days=DATE_TOLERANCE)).strftime("%Y-%m-%dT23:59:59Z")

    candidates = []
    for keyword in keywords:
        data = _graph_get(
            f"{GRAPH_BASE}/me/mailFolders/{folder_id}/messages",
            token,
            {
                "$filter": f"receivedDateTime ge {date_from} and receivedDateTime le {date_to}",
                "$search": f'"{keyword}"',
                "$select": "id,subject,receivedDateTime,from,hasAttachments",
                "$top": "10",
                "$orderby": "receivedDateTime desc",
            },
        )
        for msg in data.get("value", []):
            if msg.get("hasAttachments"):
                candidates.append(msg)

        if candidates:
            break  # Erster Keyword-Treffer reicht

        time.sleep(0.3)  # Rate-Limiting

    return candidates


def download_attachments(token: str, message_id: str, download_dir: Path, prefix: str = "") -> list[Path]:
    """Lädt PDF-Anhänge einer Email herunter."""
    data = _graph_get(
        f"{GRAPH_BASE}/me/messages/{message_id}/attachments",
        token,
        {"$select": "id,name,contentType,contentBytes,size"},
    )

    downloaded = []
    for att in data.get("value", []):
        name = att.get("name", "")
        content_type = att.get("contentType", "")
        size = att.get("size", 0)

        # Nur PDFs, max 10 MB
        if not (name.lower().endswith(".pdf") or "pdf" in content_type.lower()):
            continue
        if size > 10 * 1024 * 1024:
            continue

        content_bytes = att.get("contentBytes")
        if not content_bytes:
            continue

        import base64
        pdf_bytes = base64.b64decode(content_bytes)

        safe_name = re.sub(r"[^\w.\-]", "_", name)
        save_path = download_dir / f"{prefix}{safe_name}"
        save_path.write_bytes(pdf_bytes)
        downloaded.append(save_path)

    return downloaded


# ─── Orchestrator ───────────────────────────────────────────────────

def match_and_download_receipts(
    token: str,
    entries: list[dict],
    download_dir: Path,
) -> dict:
    """
    Sucht für jeden MC-Eintrag passende Belege im Outlook-Ordner
    und lädt die PDF-Anhänge herunter.

    Returns:
        {
            "matched": [{"entry": ..., "email_subject": ..., "files": [...]}],
            "unmatched": [entry, ...],
            "downloaded_files": [Path, ...],
        }
    """
    download_dir.mkdir(parents=True, exist_ok=True)

    # 1. Belege-Ordner finden
    print(f"\n📂 Suche Outlook-Ordner '{BELEGE_FOLDER}' ...")
    folder_id = find_mail_folder(token, BELEGE_FOLDER)
    if not folder_id:
        print(f"  ❌ Ordner '{BELEGE_FOLDER}' nicht gefunden!")
        return {"matched": [], "unmatched": entries, "downloaded_files": []}
    print(f"  ✅ Ordner gefunden")

    matched = []
    unmatched = []
    all_files = []

    # Nur Belastungen (keine Gutschriften)
    debits = [e for e in entries if not e.get("is_credit")]
    print(f"  🔍 Suche Belege für {len(debits)} Einträge ...\n")

    for idx, entry in enumerate(debits, 1):
        vendor = entry.get("vendor", "?")
        amount = entry.get("amount", 0)
        date = entry.get("date", "")
        print(f"  [{idx}/{len(debits)}] {vendor:<30s}  {amount:>8.2f} EUR  ({date})")

        candidates = search_receipts_for_entry(token, folder_id, entry)
        if not candidates:
            print(f"         ⚠️  Kein passender Beleg gefunden")
            unmatched.append(entry)
            continue

        # Besten Kandidaten nehmen (erster Treffer mit Anhang)
        msg = candidates[0]
        subject = msg.get("subject", "")[:60]
        print(f"         ✅ {subject}")

        # PDF-Anhänge herunterladen
        date_prefix = date.replace(".", "") + "_" if date else ""
        vendor_short = re.sub(r"[^\w]", "", vendor)[:20]
        prefix = f"{date_prefix}{vendor_short}_"

        files = download_attachments(token, msg["id"], download_dir, prefix)
        if files:
            for f in files:
                print(f"         📎 {f.name}")
            all_files.extend(files)
            matched.append({
                "entry": entry,
                "email_subject": msg.get("subject", ""),
                "files": files,
            })
        else:
            print(f"         ⚠️  Email gefunden aber kein PDF-Anhang")
            unmatched.append(entry)

        time.sleep(0.3)  # Rate-Limiting

    print(f"\n{'=' * 60}")
    print(f"  Belege: {len(matched)} gefunden, {len(unmatched)} offen")
    print(f"  PDFs heruntergeladen: {len(all_files)}")
    print(f"{'=' * 60}")

    return {
        "matched": matched,
        "unmatched": unmatched,
        "downloaded_files": all_files,
    }
