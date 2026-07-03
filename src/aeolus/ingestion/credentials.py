"""Dhan credential loading (TASK-002 ADR).

Credential rotation is owned entirely by the external refresh daemon
(~/Documents/Obsidian/Tools/Dhan Refresh token/), already running against
this same Supabase instance for the Ares project. This module only reads —
AEOLUS never generates or renews Dhan tokens itself.
"""

from __future__ import annotations

from dataclasses import dataclass

from supabase import Client, create_client

TABLE = "api_keys"
PROVIDER = "DHAN"


@dataclass(frozen=True)
class DhanCredentials:
    client_id: str
    access_token: str


class CredentialsSource:
    """Loads Dhan client_id/access_token from the shared `api_keys` table."""

    def __init__(self, supabase_url: str, supabase_key: str) -> None:
        self._client: Client = create_client(supabase_url, supabase_key)

    def load(self) -> DhanCredentials:
        """Fetch current credentials. Call again on a 401 to pick up rotation."""
        response = (
            self._client.table(TABLE)
            .select("client_id, access_token")
            .eq("provider", PROVIDER)
            .single()
            .execute()
        )
        row = response.data
        assert isinstance(row, dict)
        return DhanCredentials(
            client_id=str(row["client_id"]), access_token=str(row["access_token"])
        )
