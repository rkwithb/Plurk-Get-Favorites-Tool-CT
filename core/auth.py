# Copyright (c) 2026 rkwithb (https://github.com/rkwithb)
# Licensed under CC BY-NC 4.0 (Non-Commercial Use Only)
# Disclaimer: Use at your own risk. The author is not responsible for any damages.

"""
core/auth.py

API key management and OAuth flow for plurk-fav.

- get_keys()           : read all four keys from tool.env
- save_keys()          : write all four keys to tool.env (full overwrite)
- build_plurk_client() : construct an authorised PlurkAPI instance
- start_oauth()        : get request token, return browser URL
- finish_oauth()       : exchange verifier for access token, return key pair

OAuth flow (GUI-driven, no interactive prompts):

    url = start_oauth(ck, cs)
      → PlurkAPI.get_request_token()    # network
      → PlurkAPI.get_verifier_url()     # returns URL string
      → GUI opens URL in browser
      → CTkToplevel dialog: user pastes verifier code

    at, ats = finish_oauth(client, verifier)
      → PlurkAPI.get_access_token(verifier)   # network
      → returns (access_token, token_secret)
      → GUI fills fields and calls save_keys() with all four values

Library's interactive methods (get_verifier, get_consumer_token) are
never called — they block on stdin and are incompatible with the GUI.
"""

import os
from typing import Tuple

from dotenv import load_dotenv, set_key
from plurk_oauth import PlurkAPI

from core.logger import get_logger
from core.paths import ENV_PATH

logger = get_logger()

# Env variable names — single source of truth
_KEY_CK  = "PLURK_CONSUMER_KEY"
_KEY_CS  = "PLURK_CONSUMER_SECRET"
_KEY_AT  = "PLURK_ACCESS_TOKEN"
_KEY_ATS = "PLURK_ACCESS_TOKEN_SECRET"


# ---------------------------------------------------------------------------
# Key management
# ---------------------------------------------------------------------------

def get_keys() -> Tuple[str, str, str, str]:
    """
    Read all four API keys from tool.env.
    Creates an empty tool.env template if the file does not exist.

    Returns:
        (consumer_key, consumer_secret, access_token, access_token_secret)
        Any missing value is returned as an empty string — callers should
        check for completeness before using the keys.
    """
    if not os.path.exists(ENV_PATH):
        # Create empty template so the user has a file to reference
        logger.info("auth: tool.env not found — creating empty template at %s", ENV_PATH)
        set_key(ENV_PATH, _KEY_CK,  "", quote_mode="never")
        set_key(ENV_PATH, _KEY_CS,  "", quote_mode="never")
        set_key(ENV_PATH, _KEY_AT,  "", quote_mode="never")
        set_key(ENV_PATH, _KEY_ATS, "", quote_mode="never")
        return "", "", "", ""

    load_dotenv(ENV_PATH, override=True)

    ck  = os.getenv(_KEY_CK,  "").strip()
    cs  = os.getenv(_KEY_CS,  "").strip()
    at  = os.getenv(_KEY_AT,  "").strip()
    ats = os.getenv(_KEY_ATS, "").strip()

    logger.debug(
        "auth: keys loaded — ck=%s cs=%s at=%s ats=%s",
        bool(ck), bool(cs), bool(at), bool(ats),
    )
    return ck, cs, at, ats


def save_keys(ck: str, cs: str, at: str, ats: str) -> None:
    """
    Write all four API keys to tool.env (full overwrite of all key fields).
    Creates tool.env if it does not exist.

    Called in two situations:
    - User clicks [Save Keys] in the setup panel
    - After a successful OAuth exchange (finish_oauth returns the token pair)

    Args:
        ck:  consumer key
        cs:  consumer secret
        at:  access token
        ats: access token secret
    """
    # set_key from python-dotenv writes individual keys cleanly without
    # touching unrelated lines — safe to call multiple times in sequence
    set_key(ENV_PATH, _KEY_CK,  ck,  quote_mode="never")
    set_key(ENV_PATH, _KEY_CS,  cs,  quote_mode="never")
    set_key(ENV_PATH, _KEY_AT,  at,  quote_mode="never")
    set_key(ENV_PATH, _KEY_ATS, ats, quote_mode="never")

    logger.debug("auth: keys saved to %s", ENV_PATH)


# ---------------------------------------------------------------------------
# PlurkAPI client
# ---------------------------------------------------------------------------

def build_plurk_client(ck: str, cs: str, at: str, ats: str) -> PlurkAPI:
    """
    Construct and return an authorised PlurkAPI instance.

    Args:
        ck:  consumer key
        cs:  consumer secret
        at:  access token
        ats: access token secret

    Returns:
        PlurkAPI instance ready for callAPI() calls.

    Raises:
        ValueError: if ck or cs are empty (library requirement)
        Exception:  if the authorize() call fails (network or bad token)
    """
    client = PlurkAPI(ck, cs)
    client.authorize(at, ats)
    logger.debug("auth: PlurkAPI client authorised")
    return client


# ---------------------------------------------------------------------------
# OAuth flow
# ---------------------------------------------------------------------------

def start_oauth(ck: str, cs: str) -> Tuple[PlurkAPI, str]:
    """
    Begin the OAuth handshake. Fetches a request token and returns the
    browser URL the user must visit to authorise the app.

    Args:
        ck: consumer key
        cs: consumer secret

    Returns:
        (client, url) — the PlurkAPI instance (needed for finish_oauth)
        and the authorisation URL string to open in the browser.

    Raises:
        ValueError: if ck or cs are empty
        Exception:  on network failure during get_request_token()
    """
    client = PlurkAPI(ck, cs)
    client.get_request_token()
    url = client.get_verifier_url()
    logger.debug("auth: OAuth started — verifier URL obtained")
    return client, url


def finish_oauth(client: PlurkAPI, verifier: str) -> Tuple[str, str]:
    """
    Complete the OAuth handshake by exchanging the verifier code for
    an access token pair.

    Args:
        client:   the PlurkAPI instance returned by start_oauth()
        verifier: the verification code the user obtained from the browser

    Returns:
        (access_token, access_token_secret) as plain strings.
        The caller (GUI) is responsible for calling save_keys() with
        all four values after receiving this result.

    Raises:
        Exception: on network failure or invalid verifier code
    """
    token = client.get_access_token(verifier.strip())
    at  = token["key"]
    ats = token["secret"]
    logger.debug("auth: OAuth complete — access token obtained")
    return at, ats
