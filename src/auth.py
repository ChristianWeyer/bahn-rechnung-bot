"""Microsoft Graph OAuth Token-Management (MSAL)."""

import sys

import msal

from src.config import TOKEN_CACHE_FILE, AZURE_CLIENT_ID, AZURE_TENANT_ID, SCOPES


def _get_token_cache() -> msal.SerializableTokenCache:
    """Lädt oder erstellt den MSAL Token-Cache."""
    cache = msal.SerializableTokenCache()
    if TOKEN_CACHE_FILE.exists():
        cache.deserialize(TOKEN_CACHE_FILE.read_text())
    return cache


def _save_token_cache(cache: msal.SerializableTokenCache):
    """Speichert den Token-Cache mit eingeschränkten Dateiberechtigungen."""
    if cache.has_state_changed:
        TOKEN_CACHE_FILE.write_text(cache.serialize())
        TOKEN_CACHE_FILE.chmod(0o600)


def get_graph_token() -> str:
    """
    Holt ein gültiges Access-Token für Microsoft Graph.
    Beim ersten Mal: Device Code Flow (Browser-Login).
    Danach: automatisch per Refresh-Token.
    """
    cache = _get_token_cache()

    app = msal.PublicClientApplication(
        AZURE_CLIENT_ID,
        authority=f"https://login.microsoftonline.com/{AZURE_TENANT_ID}",
        token_cache=cache,
    )

    # Versuch 1: Token aus Cache (silent)
    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(SCOPES, account=accounts[0])
        if result and "access_token" in result:
            _save_token_cache(cache)
            return result["access_token"]

    # Versuch 2: Device Code Flow (interaktiv)
    print("\n🔐 Microsoft-Anmeldung erforderlich (einmalig)")
    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        print(f"  ❌ Device Code Flow fehlgeschlagen: {flow}")
        sys.exit(1)

    print(f"  → Öffne: {flow['verification_uri']}")
    print(f"  → Code:  {flow['user_code']}")
    print(f"  Warte auf Anmeldung im Browser ...")

    result = app.acquire_token_by_device_flow(flow)

    if "access_token" not in result:
        print(f"  ❌ Anmeldung fehlgeschlagen: {result.get('error_description', result)}")
        sys.exit(1)

    _save_token_cache(cache)
    print("  ✅ Anmeldung erfolgreich! Token wird gecacht.")
    return result["access_token"]
