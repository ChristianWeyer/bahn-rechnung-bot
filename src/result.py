"""Zentrale Ergebnis-Datenstruktur für die Entry→PDF Zuordnung."""

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class EntryResult:
    """Ergebnis für einen einzelnen MC-Eintrag."""
    entry: dict                          # Der MC-Eintrag (vendor, amount, date, ...)
    status: str = "pending"              # pending | matched | unmatched | link_only
    source: str = ""                     # Quelle: "outlook", "bahn", "amazon", "portal:openai", "heise", ...
    files: list[Path] = field(default_factory=list)  # Zugehörige PDFs
    receipt_url: str = ""                # Falls nur Link (kein PDF)
    email_subject: str = ""              # Betreff der gematchten Email
    note: str = ""                       # Zusätzliche Info

    @property
    def vendor(self) -> str:
        return self.entry.get("vendor", "?")

    @property
    def amount(self) -> float:
        return self.entry.get("amount", 0)

    @property
    def date(self) -> str:
        return self.entry.get("date", "")

    @property
    def is_db(self) -> bool:
        return self.entry.get("category") == "db"

    @property
    def is_credit(self) -> bool:
        return self.entry.get("is_credit", False)


@dataclass
class RunResult:
    """Gesamtergebnis eines Bot-Laufs — trackt alle Einträge und ihre Belege."""
    mc_pdf_name: str = ""
    entries: list[EntryResult] = field(default_factory=list)

    def add_entries(self, raw_entries: list[dict]):
        """Fügt MC-Einträge als pending hinzu."""
        for e in raw_entries:
            self.entries.append(EntryResult(entry=e))

    def mark_matched(self, entry: dict, files: list[Path], source: str, **kwargs):
        """Markiert einen Eintrag als gematcht mit PDFs."""
        for er in self.entries:
            if er.entry is entry and er.status == "pending":
                er.status = "matched"
                er.files = files
                er.source = source
                er.email_subject = kwargs.get("email_subject", "")
                er.note = kwargs.get("note", "")
                return
        # Fallback: nach vendor+amount+date matchen
        for er in self.entries:
            if (er.status == "pending"
                and er.vendor == entry.get("vendor", "")
                and abs(er.amount - entry.get("amount", 0)) < 0.01
                and er.date == entry.get("date", "")):
                er.status = "matched"
                er.files = files
                er.source = source
                er.email_subject = kwargs.get("email_subject", "")
                er.note = kwargs.get("note", "")
                return

    def mark_link_only(self, entry: dict, receipt_url: str, source: str, **kwargs):
        """Markiert einen Eintrag als 'Link vorhanden, aber kein PDF'."""
        for er in self.entries:
            if er.entry is entry and er.status == "pending":
                er.status = "link_only"
                er.receipt_url = receipt_url
                er.source = source
                er.email_subject = kwargs.get("email_subject", "")
                return

    def mark_unmatched(self, entry: dict):
        """Markiert einen Eintrag explizit als nicht gefunden."""
        for er in self.entries:
            if er.entry is entry and er.status == "pending":
                er.status = "unmatched"
                return

    # ─── Abfragen ──────────────────────────────────────────

    @property
    def db_entries(self) -> list[EntryResult]:
        return [e for e in self.entries if e.is_db and not e.is_credit]

    @property
    def non_db_entries(self) -> list[EntryResult]:
        return [e for e in self.entries if not e.is_db and not e.is_credit]

    @property
    def matched(self) -> list[EntryResult]:
        return [e for e in self.entries if e.status == "matched" and not e.is_credit]

    @property
    def unmatched(self) -> list[EntryResult]:
        return [e for e in self.entries if e.status in ("unmatched", "pending") and not e.is_credit]

    @property
    def link_only(self) -> list[EntryResult]:
        return [e for e in self.entries if e.status == "link_only"]

    @property
    def all_files(self) -> list[Path]:
        files = []
        for e in self.entries:
            files.extend(e.files)
        return files

    @property
    def total_debits(self) -> int:
        return len([e for e in self.entries if not e.is_credit])

    def summary(self) -> str:
        """Kurzübersicht für Console-Output."""
        total = self.total_debits
        matched = len(self.matched)
        unmatched = len(self.unmatched)
        link_only = len(self.link_only)
        files = len(self.all_files)
        return f"{matched}/{total} Belege gefunden ({files} PDFs), {unmatched} offen, {link_only} nur Link"
