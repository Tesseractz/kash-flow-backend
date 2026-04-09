import os
import secrets
import string
import time
from typing import List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import app.core.password_crypto as password_crypto
import app.services.subscriptions as subscriptions
import app.db.supabase as supabase_client
from app.api.deps import RequestContext, get_current_context

router = APIRouter()


class UserResponse(BaseModel):
    id: str
    email: str
    name: Optional[str] = None
    role: str
    created_at: Optional[str] = None
    password: Optional[str] = None
    login_username: Optional[str] = None


class InviteUserRequest(BaseModel):
    role: str = "cashier"


class UpdateUserRoleRequest(BaseModel):
    role: str


@router.get("/users", response_model=List[UserResponse])
def list_users(ctx: RequestContext = Depends(get_current_context)):
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")
    limits = subscriptions.get_store_plan(ctx.store_id)
    if not limits.is_active and not limits.is_on_trial:
        raise HTTPException(status_code=402, detail="Team management requires Pro plan")

    supabase = supabase_client.get_supabase_client()

    try:
        prof_result = supabase.table("profiles").select("*").eq("store_id", ctx.store_id).execute()

        if not prof_result.data:
            return []

        supabase_url = os.getenv("SUPABASE_URL")
        service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

        users = []
        for profile in prof_result.data:
            user_id = profile["id"]
            email = "unknown"
            created_at = None

            try:
                with httpx.Client() as client:
                    resp = client.get(
                        f"{supabase_url}/auth/v1/admin/users/{user_id}",
                        headers={
                            "apikey": service_key,
                            "Authorization": f"Bearer {service_key}",
                        },
                        timeout=5,
                    )
                    if resp.status_code == 200:
                        user_data = resp.json()
                        email = user_data.get("email", "unknown")
                        created_at = user_data.get("created_at")
            except Exception:
                pass

            users.append(
                {
                    "id": user_id,
                    "email": email,
                    "name": profile.get("name"),
                    "role": profile.get("role", "cashier"),
                    "created_at": created_at,
                }
            )

        return users
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/users/invite", response_model=UserResponse)
def invite_user(payload: InviteUserRequest, ctx: RequestContext = Depends(get_current_context)):
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")

    if payload.role not in ("admin", "cashier"):
        raise HTTPException(status_code=400, detail="Role must be 'admin' or 'cashier'")

    supabase = supabase_client.get_supabase_client()

    limits = subscriptions.get_store_plan(ctx.store_id)
    current_users = supabase.table("profiles").select("id").eq("store_id", ctx.store_id).execute()
    current_user_count = len(current_users.data or [])

    if current_user_count >= limits.max_users:
        plan_name = limits.plan.capitalize() if limits.plan != "expired" else "Free"
        raise HTTPException(
            status_code=402,
            detail=f"User limit reached ({limits.max_users} users for {plan_name} plan). Upgrade your plan to add more team members.",
        )

    supabase_url = os.getenv("SUPABASE_URL")
    service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

    try:
        existing_profiles = supabase.table("profiles").select("name").eq("store_id", ctx.store_id).execute()
        existing_names = [p.get("name", "").lower() for p in (existing_profiles.data or [])]

        base_name = "cashier" if payload.role == "cashier" else "admin"
        username = base_name
        counter = 1
        while username.lower() in existing_names or any(name.startswith(username.lower()) for name in existing_names):
            username = f"{base_name}{counter}"
            counter += 1
    except Exception:
        username = f"cashier{secrets.randbelow(10000)}" if payload.role == "cashier" else f"admin{secrets.randbelow(10000)}"

    email = f"{username}@store.local"
    name = username

    try:
        with httpx.Client() as client:
            resp = client.get(
                f"{supabase_url}/auth/v1/admin/users",
                headers={
                    "apikey": service_key,
                    "Authorization": f"Bearer {service_key}",
                },
                params={"page": 1, "per_page": 1000},
                timeout=10,
            )

            existing_user = None
            if resp.status_code == 200:
                data = resp.json()
                users = data.get("users", [])
                for user in users:
                    if user.get("email") == email:
                        existing_user = user
                        break

            if existing_user:
                user_id = existing_user.get("id")

                existing_profile = supabase.table("profiles").select("*").eq("id", user_id).eq("store_id", ctx.store_id).execute()
                if existing_profile.data:
                    raise HTTPException(status_code=400, detail="User is already a member of this store")

                password = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(12))

                try:
                    encrypted_password = password_crypto._encrypt_password(password)
                except Exception:
                    encrypted_password = None

                profile_data = {
                    "id": user_id,
                    "name": name,
                    "role": payload.role,
                    "store_id": ctx.store_id,
                }
                if encrypted_password:
                    profile_data["temp_password_encrypted"] = encrypted_password

                profile_data_insert = {k: v for k, v in profile_data.items() if k != "temp_password_encrypted"}
                supabase.table("profiles").insert(profile_data_insert).execute()

                if encrypted_password:
                    time.sleep(0.3)
                    try:
                        supabase.table("profiles").update({"temp_password_encrypted": encrypted_password}).eq("id", user_id).execute()
                    except Exception:
                        pass

                return {
                    "id": user_id,
                    "email": email,
                    "name": name,
                    "role": payload.role,
                    "created_at": existing_user.get("created_at"),
                    "password": password,
                    "login_username": email,
                }

            password = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(12))

            resp = client.post(
                f"{supabase_url}/auth/v1/admin/users",
                headers={
                    "apikey": service_key,
                    "Authorization": f"Bearer {service_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "email": email,
                    "password": password,
                    "email_confirm": True,
                    "user_metadata": {"store_name": "Invited User", "username": username},
                },
                timeout=10,
            )

            if resp.status_code not in (200, 201):
                raise HTTPException(status_code=500, detail=f"Failed to create user: {resp.text}")

            user_data = resp.json()
            user_id = user_data.get("id")

        try:
            encrypted_password = password_crypto._encrypt_password(password)
        except Exception:
            encrypted_password = None

        profile_data = {
            "id": user_id,
            "name": name,
            "role": payload.role,
            "store_id": ctx.store_id,
        }
        if encrypted_password:
            profile_data["temp_password_encrypted"] = encrypted_password

        try:
            profile_data_insert = {k: v for k, v in profile_data.items() if k != "temp_password_encrypted"}
            supabase.table("profiles").insert(profile_data_insert).execute()

            if encrypted_password:
                time.sleep(0.3)
                try:
                    supabase.table("profiles").update({"temp_password_encrypted": encrypted_password}).eq("id", user_id).execute()
                except Exception:
                    pass
        except Exception as e:
            error_msg = str(e)
            if "temp_password_encrypted" in error_msg or "column" in error_msg.lower() or "does not exist" in error_msg.lower():
                profile_data.pop("temp_password_encrypted", None)
                supabase.table("profiles").insert(profile_data).execute()
            else:
                raise

        return {
            "id": user_id,
            "email": email,
            "name": name,
            "role": payload.role,
            "created_at": user_data.get("created_at"),
            "password": password,
            "login_username": email,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to invite user: {str(e)}")


class UserCredentialsResponse(BaseModel):
    id: str
    email: str
    name: Optional[str] = None
    login_username: str
    password: str


@router.get("/users/{user_id}/credentials", response_model=UserCredentialsResponse)
def get_user_credentials(user_id: str, ctx: RequestContext = Depends(get_current_context)):
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")

    supabase = supabase_client.get_supabase_client()

    try:
        prof_result = supabase.table("profiles").select("*").eq("id", user_id).eq("store_id", ctx.store_id).single().execute()

        if not prof_result.data:
            raise HTTPException(status_code=404, detail="User not found in this store")

        profile = prof_result.data

        supabase_url = os.getenv("SUPABASE_URL")
        service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

        with httpx.Client() as client:
            resp = client.get(
                f"{supabase_url}/auth/v1/admin/users/{user_id}",
                headers={
                    "apikey": service_key,
                    "Authorization": f"Bearer {service_key}",
                },
                timeout=10,
            )

            if resp.status_code != 200:
                raise HTTPException(status_code=404, detail="User not found in auth system")

            user_data = resp.json()
            email = user_data.get("email", "")

        encrypted_password = profile.get("temp_password_encrypted")
        if not encrypted_password:
            raise HTTPException(
                status_code=404,
                detail="Password not stored for this user. This user may have been created before password storage was enabled, or they may have changed their password.",
            )

        password = password_crypto._decrypt_password(encrypted_password)

        return {
            "id": user_id,
            "email": email,
            "name": profile.get("name"),
            "login_username": email,
            "password": password,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get credentials: {str(e)}")


@router.put("/users/{user_id}/role", response_model=UserResponse)
def update_user_role(user_id: str, payload: UpdateUserRoleRequest, ctx: RequestContext = Depends(get_current_context)):
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")

    if payload.role not in ("admin", "cashier"):
        raise HTTPException(status_code=400, detail="Role must be 'admin' or 'cashier'")

    supabase = supabase_client.get_supabase_client()

    try:
        prof_result = supabase.table("profiles").select("*").eq("id", user_id).eq("store_id", ctx.store_id).single().execute()

        if not prof_result.data:
            raise HTTPException(status_code=404, detail="User not found in this store")

        if payload.role == "cashier" and prof_result.data.get("role") == "admin":
            admin_count = (
                supabase.table("profiles")
                .select("id", count="exact")
                .eq("store_id", ctx.store_id)
                .eq("role", "admin")
                .execute()
            )
            if admin_count.count == 1:
                raise HTTPException(
                    status_code=400,
                    detail="Cannot remove the last admin. Promote another user to admin first.",
                )

        supabase.table("profiles").update({"role": payload.role}).eq("id", user_id).eq("store_id", ctx.store_id).execute()

        updated = supabase.table("profiles").select("*").eq("id", user_id).single().execute()

        supabase_url = os.getenv("SUPABASE_URL")
        service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        email = "unknown"

        try:
            with httpx.Client() as client:
                resp = client.get(
                    f"{supabase_url}/auth/v1/admin/users/{user_id}",
                    headers={
                        "apikey": service_key,
                        "Authorization": f"Bearer {service_key}",
                    },
                    timeout=5,
                )
                if resp.status_code == 200:
                    user_data = resp.json()
                    email = user_data.get("email", "unknown")
        except Exception:
            pass

        return {
            "id": user_id,
            "email": email,
            "name": updated.data.get("name"),
            "role": payload.role,
            "created_at": None,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/users/{user_id}", status_code=204)
def remove_user(user_id: str, ctx: RequestContext = Depends(get_current_context)):
    if ctx.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")

    if user_id == ctx.user_id:
        raise HTTPException(status_code=400, detail="Cannot remove yourself")

    supabase = supabase_client.get_supabase_client()

    try:
        prof_result = supabase.table("profiles").select("*").eq("id", user_id).eq("store_id", ctx.store_id).single().execute()

        if not prof_result.data:
            raise HTTPException(status_code=404, detail="User not found in this store")

        if prof_result.data.get("role") == "admin":
            admin_count = (
                supabase.table("profiles")
                .select("id", count="exact")
                .eq("store_id", ctx.store_id)
                .eq("role", "admin")
                .execute()
            )
            if admin_count.count == 1:
                raise HTTPException(status_code=400, detail="Cannot remove the last admin")

        supabase.table("profiles").delete().eq("id", user_id).eq("store_id", ctx.store_id).execute()

        return None
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
