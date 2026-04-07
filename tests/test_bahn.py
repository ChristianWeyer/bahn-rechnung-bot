"""Tests für src/bahn.py und src/history.py — Hashing, History, Cleanup, Login."""

import pytest
from pathlib import Path
from src.timer import Timer
from src.history import file_hash, _file_hash_md5, is_known_file, cleanup_old_invoices
from src.config import _op_read, _get_secret


class TestTimer:
    def test_format_seconds(self):
        assert Timer._fmt(5.3) == "5.3s"
        assert Timer._fmt(59.9) == "59.9s"

    def test_format_minutes(self):
        assert Timer._fmt(65.0) == "1m 5.0s"
        assert Timer._fmt(130.5) == "2m 10.5s"


class TestFileHash:
    def test_hash_consistency(self, tmp_path):
        f = tmp_path / "test.pdf"
        f.write_text("test content")
        h1 = file_hash(f)
        h2 = file_hash(f)
        assert h1 == h2
        assert len(h1) == 64  # SHA256

    def test_different_files_different_hash(self, tmp_path):
        f1 = tmp_path / "a.pdf"
        f2 = tmp_path / "b.pdf"
        f1.write_text("content a")
        f2.write_text("content b")
        assert file_hash(f1) != file_hash(f2)


class TestHistory:
    def test_is_known_file_sha256(self, tmp_path):
        f = tmp_path / "test.pdf"
        f.write_text("test")
        h = file_hash(f)
        assert is_known_file(f, {h})

    def test_is_known_file_md5_compat(self, tmp_path):
        f = tmp_path / "test.pdf"
        f.write_text("test")
        old_hash = _file_hash_md5(f)
        assert is_known_file(f, {old_hash})

    def test_unknown_file(self, tmp_path):
        f = tmp_path / "test.pdf"
        f.write_text("test")
        assert not is_known_file(f, set())


class TestCleanup:
    def test_cleanup_old_files(self, tmp_path):
        import os, time
        import src.history as history_mod

        old = tmp_path / "old.pdf"
        new = tmp_path / "new.pdf"
        old.write_text("old")
        new.write_text("new")

        old_time = time.time() - (60 * 86400)
        os.utime(old, (old_time, old_time))

        orig = history_mod.DOWNLOAD_DIR
        history_mod.DOWNLOAD_DIR = tmp_path
        try:
            cleanup_old_invoices(30)
            assert not old.exists()
            assert new.exists()
        finally:
            history_mod.DOWNLOAD_DIR = orig


class TestCredentials:
    def test_op_read_nonexistent(self):
        assert _op_read("op://NonExistent/Item/field") is None

    def test_get_secret_env_priority(self):
        import os
        os.environ["TEST_SECRET_XYZ"] = "from_env"
        try:
            assert _get_secret("TEST_SECRET_XYZ", "op://doesnt/matter") == "from_env"
        finally:
            del os.environ["TEST_SECRET_XYZ"]


# ─── Live-Tests ────────────────────────────────────────────

@pytest.fixture
def live(request):
    if not request.config.getoption("--live"):
        pytest.skip("Live-Test übersprungen (nutze --live)")


class TestLiveBahn:
    def test_bahn_login(self, live):
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).parent.parent / ".env")
        from src.config import BAHN_EMAIL, BAHN_PASSWORD
        from src.bahn import login
        from playwright.sync_api import sync_playwright

        if not BAHN_EMAIL or not BAHN_PASSWORD:
            pytest.skip("BAHN_EMAIL/BAHN_PASSWORD nicht konfiguriert")

        timer = Timer()
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(Path(__file__).parent.parent / ".browser-data"),
                headless=True, accept_downloads=True, locale="de-DE",
            )
            page = context.new_page()
            try:
                login(page, timer)
            finally:
                context.close()
