import os
from typing import Optional
from supabase import create_client, Client
from dotenv import load_dotenv

_client: Optional[Client] = None


def get_supabase_client() -> Client:
    """
    Returns a singleton Supabase client configured from environment variables.
    """
    global _client
    if _client is not None:
        return _client

    # Load .env if present
    load_dotenv()

    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

    if not supabase_url or not supabase_key:
        raise RuntimeError(
            "Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY environment variables."
        )

    _client = create_client(supabase_url, supabase_key)
    return _client


