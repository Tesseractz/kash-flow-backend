from typing import Optional
from fastapi import Header, HTTPException, status
from .auth import verify_supabase_jwt
from .supabase_client import get_supabase_client


class RequestContext:
    def __init__(self, user_id: str, store_id: str, role: str):
        self.user_id = user_id
        self.store_id = store_id
        self.role = role


def _create_store_and_profile(supa, user_id: str, user_metadata: dict) -> dict:
    """Create store, profile, and subscription for a new user."""
    store_name = user_metadata.get("store_name", "My Store")
    
    store_result = supa.table("stores").insert({
        "name": store_name,
        "owner_id": user_id
    }).execute()
    
    if not store_result.data:
        raise HTTPException(status_code=500, detail="Failed to create store")
    
    store_id = store_result.data[0]["id"]
    
    profile_result = supa.table("profiles").insert({
        "id": user_id,
        "name": user_metadata.get("email", "User"),
        "role": "admin",
        "store_id": store_id
    }).execute()
    
    if not profile_result.data:
        supa.table("stores").delete().eq("id", store_id).execute()
        raise HTTPException(status_code=500, detail="Failed to create profile")
    
    supa.table("subscriptions").insert({
        "store_id": store_id,
        "plan": "free",
        "status": "active"
    }).execute()
    
    return profile_result.data[0]


def get_current_context(authorization: Optional[str] = Header(None)) -> RequestContext:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token")
    token = authorization.split(" ", 1)[1]
    
    try:
        payload = verify_supabase_jwt(token)
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {str(e)}")
    
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token payload")

    supa = get_supabase_client()
    
    try:
        prof_result = supa.table("profiles").select("*").eq("id", user_id).execute()
    except Exception as e:
        print(f"[get_current_context] Error querying profiles: {e}")
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    
    if prof_result.data and len(prof_result.data) > 0:
        profile = prof_result.data[0]
    else:
        user_metadata = payload.get("user_metadata", {})
        if not user_metadata:
            user_metadata = {"store_name": "My Store", "email": payload.get("email", "User")}
        
        # Handle race condition: profile might be created by parallel request
        try:
            profile = _create_store_and_profile(supa, user_id, user_metadata)
        except Exception as e:
            # If duplicate key error, fetch existing profile
            if "23505" in str(e) or "duplicate" in str(e).lower():
                prof_result = supa.table("profiles").select("*").eq("id", user_id).execute()
                if prof_result.data and len(prof_result.data) > 0:
                    profile = prof_result.data[0]
                else:
                    raise HTTPException(status_code=500, detail="Failed to get or create profile")
            else:
                print(f"[get_current_context] Error creating profile: {e}")
                raise HTTPException(status_code=500, detail=f"Failed to create profile: {str(e)}")
    
    store_id = profile.get("store_id")
    role = profile.get("role", "cashier")
    
    if not store_id:
        raise HTTPException(status_code=500, detail="Profile missing store_id")
    
    return RequestContext(user_id=user_id, store_id=store_id, role=role)
