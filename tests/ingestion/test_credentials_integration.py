"""Live integration test against the real shared Supabase `api_keys` table
(same instance/pattern as Ares -- see ~/Documents/Obsidian/Tools/Dhan Refresh
token/). No mocking. Skipped if SUPABASE_URL/SUPABASE_KEY are missing.
"""

import pytest
from dotenv import dotenv_values

from aeolus.ingestion.credentials import CredentialsSource

ENV_PATH = __file__.rsplit("/tests/", 1)[0] + "/.env"
_cfg = dotenv_values(ENV_PATH)
SUPABASE_URL = _cfg.get("SUPABASE_URL")
SUPABASE_KEY = _cfg.get("SUPABASE_KEY")

pytestmark = pytest.mark.skipif(
    not (SUPABASE_URL and SUPABASE_KEY),
    reason="SUPABASE_URL/SUPABASE_KEY not set in .env",
)


def test_load_dhan_credentials_from_shared_api_keys_table():
    source = CredentialsSource(SUPABASE_URL, SUPABASE_KEY)
    credentials = source.load()
    assert credentials.client_id
    assert credentials.access_token
