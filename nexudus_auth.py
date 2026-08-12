"""
Nexudus authentication — one-time interactive login, then silent token refresh.

Run this yourself, in your own terminal:

    python nexudus_auth.py setup

It prompts for your Nexudus email and password (hidden input, via getpass —
never echoed, never passed as a command-line argument, never seen by an
agent). It exchanges them for an OAuth2 access/refresh token pair and writes
ONLY the tokens to .env (gitignored, chmod 600) — your password is never
written to disk or stored anywhere after this script exits.

Every other script in this repo calls `get_access_token()` from here, which
transparently refreshes the access token via the refresh token when it's
close to expiring — no password re-entry needed for the life of the refresh
token (~30-90 days per Nexudus; re-run `setup` when it finally expires).

This account needs to be a Nexudus admin. `setup` checks this immediately
and fails with a clear error otherwise, rather than letting every subsequent
script fail confusingly deep into a seeding run. It also checks the
account's "API access" flag, but only warns on that one — confirmed live,
it can read False on an account that's demonstrably been authenticating and
calling the API fine the whole time, so it isn't a reliable gate.
"""

import getpass
import os
import stat
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv, set_key

PROJECT_ROOT = Path(__file__).parent
ENV_PATH = PROJECT_ROOT / ".env"
DEFAULT_BASE_URL = "https://spaces.nexudus.com"

# Refresh proactively if the access token has less than this long left.
REFRESH_MARGIN_SECONDS = 300


def base_url():
    return os.environ.get("NEXUDUS_BASE_URL", DEFAULT_BASE_URL)


def _secure_env_file():
    if ENV_PATH.exists():
        os.chmod(ENV_PATH, stat.S_IRUSR | stat.S_IWUSR)  # chmod 600


def _save_tokens(access_token, refresh_token, expires_in):
    ENV_PATH.touch(exist_ok=True)
    expires_at = str(int(time.time()) + int(expires_in))
    set_key(str(ENV_PATH), "NEXUDUS_ACCESS_TOKEN", access_token)
    set_key(str(ENV_PATH), "NEXUDUS_REFRESH_TOKEN", refresh_token)
    set_key(str(ENV_PATH), "NEXUDUS_TOKEN_EXPIRES_AT", expires_at)
    _secure_env_file()


def _check_admin_access(access_token, email):
    """Fail loudly, at setup time, if this account can't actually do the job.

    Filters by the email that just logged in — an unfiltered users list on
    an account with hundreds/thousands of users returns an arbitrary record,
    not necessarily the authenticated one.
    """
    try:
        resp = requests.get(
            f"{base_url()}/api/sys/users",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"User_Email": email, "size": 1},
            timeout=30,
        )
        resp.raise_for_status()
        records = resp.json().get("Records", [])
    except requests.exceptions.RequestException as e:
        raise SystemExit(f"Login succeeded, but the admin-access check failed: {e}")
    except ValueError:
        raise SystemExit("Login succeeded, but the admin-access check got an unreadable response.")
    if not records:
        raise SystemExit(f"Could not find a user record for {email} — unexpected API response.")

    me = records[0]
    if not me.get("IsAdmin"):
        raise SystemExit(
            f"Error: {me.get('Email')} is not a Nexudus admin. "
            "This tool needs an admin account to create records across all entities."
        )
    if me.get("APIAccess") is False:
        # Confirmed live: this field can read False on an account that has
        # clearly been authenticating and calling the API successfully all
        # along (the OAuth password grant that got us here is the real
        # proof of access) — same false-negative pattern as the other
        # Nexudus API quirks in CLAUDE.md (rules 12/27). Warn, don't block.
        print(f"! Note: Nexudus reports {me.get('Email')} as not having API access enabled "
              "(Settings > Users), but the login itself succeeded, so continuing anyway.")
    print(f"✓ Authenticated as {me.get('Email')} — admin.")


def setup():
    print(f"Nexudus login ({base_url()})")
    email = input("Email: ").strip()
    password = getpass.getpass("Password: ")

    resp = requests.post(
        f"{base_url()}/api/token",
        data={"grant_type": "password", "username": email, "password": password},
        timeout=30,
    )
    del password  # out of memory as soon as we're done with it

    if resp.status_code != 200:
        raise SystemExit(f"Login failed ({resp.status_code}): {resp.text[:300]}")

    payload = resp.json()
    _save_tokens(payload["access_token"], payload["refresh_token"], payload["expires_in"])
    print(f"✓ Tokens saved to {ENV_PATH} (chmod 600, gitignored).")

    _check_admin_access(payload["access_token"], email)
    print(f"✓ Access token valid for ~{int(payload['expires_in']) // 3600}h; "
          "will auto-refresh on future runs.")


def get_access_token():
    """Return a valid access token, refreshing via the refresh token if needed.

    Called by nexudus_client.py — not meant to be run directly.
    """
    load_dotenv(ENV_PATH, override=True)

    access_token = os.environ.get("NEXUDUS_ACCESS_TOKEN")
    refresh_token = os.environ.get("NEXUDUS_REFRESH_TOKEN")
    expires_at = int(os.environ.get("NEXUDUS_TOKEN_EXPIRES_AT", "0"))

    if not access_token or not refresh_token:
        raise SystemExit("Not authenticated. Run 'python nexudus_auth.py setup' first.")

    if time.time() < expires_at - REFRESH_MARGIN_SECONDS:
        return access_token

    resp = requests.post(
        f"{base_url()}/api/token",
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        timeout=30,
    )
    if resp.status_code != 200:
        raise SystemExit(
            f"Token refresh failed ({resp.status_code}). "
            "Run 'python nexudus_auth.py setup' again to re-authenticate."
        )

    payload = resp.json()
    _save_tokens(payload["access_token"], payload["refresh_token"], payload["expires_in"])
    return payload["access_token"]


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] != "setup":
        print("Usage: python nexudus_auth.py setup")
        sys.exit(1)
    setup()
