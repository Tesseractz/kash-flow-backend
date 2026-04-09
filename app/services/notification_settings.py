def fetch_notification_settings(supabase, store_id: str) -> dict:
    try:
        res = (
            supabase.table("notification_settings")
            .select("notification_email,low_stock_threshold,daily_summary_enabled")
            .eq("store_id", store_id)
            .single()
            .execute()
        )
        return res.data or {}
    except Exception:
        return {}
