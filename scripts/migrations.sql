-- ============================================================
-- KashPoint Supabase patch migration
-- ------------------------------------------------------------
-- Run this in Supabase SQL Editor (Project -> SQL -> New query).
-- ------------------------------------------------------------
-- Idempotent. Targeted at the 4 gaps the current backend hits:
--   1. subscriptions.trial_consumed_at        (missing column)
--   2. cookie_preferences UNIQUE (user_id)    (upsert target missing)
--   3. push_subscriptions                     (table missing)
--   4. audit_logs                             (table missing)
--
-- Your existing process_sale RPC + every other table is already
-- compatible with the backend code, so we leave them alone.
-- ============================================================

-- ------------------------------------------------------------
-- 1. subscriptions: add trial_consumed_at
--    Used by /billing/checkout + /billing/paystack/sync + the
--    Paystack webhook to enforce "one trial per store".
-- ------------------------------------------------------------
alter table public.subscriptions
    add column if not exists trial_consumed_at timestamptz;

-- ------------------------------------------------------------
-- 2. cookie_preferences: enforce one row per user_id
--    PostgREST `upsert(on_conflict="user_id")` needs a UNIQUE
--    constraint (or PK) on user_id. Your existing PK is on `id`.
--    De-duplicate first (keep most recent), then add the unique.
-- ------------------------------------------------------------
delete from public.cookie_preferences
where id in (
    select id from (
        select id,
               row_number() over (
                   partition by user_id
                   order by coalesce(updated_at, consented_at, now()) desc
               ) as rn
        from public.cookie_preferences
        where user_id is not null
    ) ranked
    where rn > 1
);

do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conname = 'cookie_preferences_user_id_key'
    ) then
        alter table public.cookie_preferences
            add constraint cookie_preferences_user_id_key unique (user_id);
    end if;
end$$;

-- ------------------------------------------------------------
-- 3. push_subscriptions: Web Push (VAPID) endpoints per device
--    Used by /push/subscribe, /push/unsubscribe, /push/test
--    and the low-stock notifier in products + sales endpoints.
-- ------------------------------------------------------------
create table if not exists public.push_subscriptions (
    id uuid primary key default gen_random_uuid(),
    store_id uuid not null references public.stores(id) on delete cascade,
    user_id uuid not null references auth.users(id) on delete cascade,
    endpoint text not null unique,
    p256dh text not null,
    auth text not null,
    user_agent text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists idx_push_subscriptions_store
    on public.push_subscriptions(store_id);
create index if not exists idx_push_subscriptions_user
    on public.push_subscriptions(user_id);

-- ------------------------------------------------------------
-- 4. audit_logs: every mutation in the backend tries to write
--    here via app/services/audit_log.py. Without the table the
--    writes silently fail and you lose the audit trail.
-- ------------------------------------------------------------
create table if not exists public.audit_logs (
    id bigserial primary key,
    store_id uuid not null references public.stores(id) on delete cascade,
    user_id uuid,
    action text not null,
    entity_type text,
    entity_id text,
    details text,
    created_at timestamptz not null default now()
);

create index if not exists idx_audit_logs_store
    on public.audit_logs(store_id);
create index if not exists idx_audit_logs_created
    on public.audit_logs(created_at);

-- ------------------------------------------------------------
-- 5. subscriptions.billing_provider default → 'paystack'
--    Existing rows are left alone; only the column default changes.
-- ------------------------------------------------------------
alter table public.subscriptions
    alter column billing_provider set default 'paystack';

-- ------------------------------------------------------------
-- 6. sales.profit
--    Persist per-sale profit so reports/exports don't have to
--    join products every time. Computed in process_sale below.
-- ------------------------------------------------------------
alter table public.sales
    add column if not exists profit numeric(12,2);

-- ------------------------------------------------------------
-- 7. process_sale: now writes subtotal + profit
--    DROP first because the function returns the `sales` row
--    type and we just changed that row's shape (added profit).
--    Signature and return type stay the same as before.
-- ------------------------------------------------------------
drop function if exists public.process_sale(uuid, bigint, integer, uuid);

create function public.process_sale(
    p_store_id uuid,
    p_product_id bigint,
    p_qty integer,
    p_sold_by uuid default null
) returns public.sales as $$
declare
    v_product record;
    v_sale public.sales%rowtype;
    v_total numeric(12,2);
    v_profit numeric(12,2);
begin
    select id, price, quantity, cost_price
        into v_product
        from public.products
        where id = p_product_id and store_id = p_store_id
        for update;

    if not found then
        raise exception 'Product not found';
    end if;

    if v_product.quantity < p_qty then
        raise exception 'Insufficient stock';
    end if;

    v_total := v_product.price * p_qty;
    v_profit := (v_product.price - coalesce(v_product.cost_price, 0)) * p_qty;

    update public.products
        set quantity = quantity - p_qty
        where id = p_product_id;

    insert into public.sales (
        store_id, product_id, quantity_sold,
        total_price, subtotal, profit, sold_by, timestamp
    ) values (
        p_store_id, p_product_id, p_qty,
        v_total, v_total, v_profit, p_sold_by, now()
    ) returning * into v_sale;

    return v_sale;
end;
$$ language plpgsql security definer;

-- Backfill profit for historical sales using the product's current cost_price.
-- Best-effort — if product was deleted, profit stays NULL.
update public.sales s
   set profit = (
       (coalesce(p.price, 0) - coalesce(p.cost_price, 0))
       * coalesce(s.quantity_sold, 0)
   )
  from public.products p
 where s.product_id = p.id
   and s.profit is null;

-- ------------------------------------------------------------
-- 8. fcm_tokens: native push tokens for the Capacitor mobile app
--    Web Push (push_subscriptions, above) doesn't work in a
--    Capacitor WebView — FCM is the only native delivery path on
--    Android. Stored separately because the data shape is different
--    (single token string, no endpoint/p256dh/auth).
-- ------------------------------------------------------------
create table if not exists public.fcm_tokens (
    id uuid primary key default gen_random_uuid(),
    store_id uuid not null references public.stores(id) on delete cascade,
    user_id uuid not null references auth.users(id) on delete cascade,
    token text not null unique,
    platform text,           -- 'android' | 'ios'
    device_info text,        -- user-agent or model string for the UI
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists idx_fcm_tokens_store on public.fcm_tokens(store_id);
create index if not exists idx_fcm_tokens_user on public.fcm_tokens(user_id);

-- ============================================================
-- Done. After running:
--  * /billing/paystack/sync stops 500-ing
--  * /privacy/cookies stops 500-ing
--  * /push/* works for browser users
--  * Every admin action lands in audit_logs
--  * New subscriptions default to billing_provider='paystack'
--  * sales.profit is recorded by process_sale (and backfilled)
--  * Mobile apps can register FCM tokens via /push/fcm/subscribe
-- ============================================================
