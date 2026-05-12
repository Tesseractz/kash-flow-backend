-- =====================================================================
-- Daily cron: process account deletions whose 30-day grace has elapsed
-- =====================================================================
-- WHY THIS EXISTS
--   POST /privacy/delete-account schedules a deletion for now()+30 days.
--   POST /privacy/scheduled-deletions/process actually deletes the user
--   and everything they own. Without something calling it on a schedule,
--   the 30-day timer never fires and accounts pile up in 'pending'.
--
-- HOW IT WORKS
--   Supabase's pg_cron extension runs SQL on a schedule. We chain it
--   with pg_net so the SQL fires an HTTPS POST at the backend with the
--   X-Cron-Secret header the endpoint requires.
--
-- ONE-TIME SETUP (run this once in Supabase SQL Editor)
--
--   1. Generate a long random string for the cron secret. In a terminal:
--        python -c "import secrets; print(secrets.token_urlsafe(48))"
--      (or any 48+ character random string you trust)
--
--   2. Set CRON_SECRET in your Render backend env to the value you
--      just generated. The backend uses it to authenticate the cron.
--
--   3. Edit the two placeholders below:
--        - BACKEND_URL_HERE   -> your Render backend URL, no trailing slash
--                                 e.g. https://kash-flow-backend.onrender.com
--        - CRON_SECRET_HERE   -> the SAME value you put in Render env
--
--   4. Run the entire script in the Supabase SQL Editor.
--      (Re-running it is safe — cron.unschedule + cron.schedule together
--       replace the prior job rather than duplicating it.)
--
-- VERIFY
--   -- the job is registered:
--   select jobname, schedule, command from cron.job
--    where jobname = 'process-scheduled-deletions';
--
--   -- recent runs (after at least one fire time has passed):
--   select start_time, status, return_message
--     from cron.job_run_details
--    where jobid = (select jobid from cron.job
--                    where jobname = 'process-scheduled-deletions')
--    order by start_time desc limit 5;
--
-- ROLLBACK
--   select cron.unschedule('process-scheduled-deletions');
-- =====================================================================

create extension if not exists pg_cron;
create extension if not exists pg_net;

-- Drop any existing job by name, then create it fresh. This makes the
-- script idempotent — safe to re-run after changing the URL or secret.
do $$
begin
    if exists (select 1 from cron.job where jobname = 'process-scheduled-deletions') then
        perform cron.unschedule('process-scheduled-deletions');
    end if;
end$$;

-- 03:00 UTC every day. Adjust the cron expression if you'd rather it
-- run at a different time (cron syntax: minute hour day month dow).
select cron.schedule(
    'process-scheduled-deletions',
    '0 3 * * *',
    $cron$
    select net.http_post(
        url := 'BACKEND_URL_HERE/privacy/scheduled-deletions/process',
        headers := jsonb_build_object(
            'X-Cron-Secret', 'CRON_SECRET_HERE',
            'Content-Type', 'application/json'
        ),
        body := '{}'::jsonb,
        timeout_milliseconds := 30000
    );
    $cron$
);
