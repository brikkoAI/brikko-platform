"""Entry point for subscription Phase 2 cron jobs.

Three jobs, dispatched by ``--job``:

  * ``--job=renewal``    — slot 1 sweep. Run hourly.
  * ``--job=reminders``  — T-3 reminder send. Run daily at 10:00 UTC.
  * ``--job=retry``      — slots 2/3 + downgrade sweep. Run every 6 h.

Why three jobs in one entrypoint
--------------------------------

The systemd template (``infra/systemd/brikko-*.timer``) calls this with
``--job=<name>``. Sharing the bootstrap (DB session factory, ЮKassa
client) keeps the per-job ``--help`` consistent and means a new job is a
one-line addition here instead of a brand-new module.

On the prod VPS the systemd ``Service`` shells out to ``docker exec
brikko-gateway python -m voltari_gateway.scripts.cron_renewal --job=...``
inside the running gateway container. See ``infra/RUNBOOK.md``
"Subscription Cron Jobs".

Exit codes
----------

  0  — job completed (even with zero work)
  1  — unknown ``--job`` value, or top-level failure
  2  — partial: at least one account-level error inside the sweep (logged)

Most monitors only need to alert on exit code != 0.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from voltari_gateway.billing.subscription_emails import (
    EmailDispatcher,
    send_renewal_reminders,
)
from voltari_gateway.billing.subscription_renewal import (
    downgrade_exhausted_subscriptions,
    renew_due_subscriptions,
    retry_failed_renewals,
)
from voltari_gateway.billing.yookassa import YooKassaClient, YooKassaConfig
from voltari_gateway.config import get_settings
from voltari_gateway.db.session import get_session_factory
from voltari_gateway.utils.logging import get_logger

log = get_logger(__name__)


def _build_yookassa_from_env() -> YooKassaClient:
    """Build a stand-alone ЮKassa client from env. Standalone so the cron
    script doesn't need to go through ``main.lifespan``."""
    settings = get_settings()
    return YooKassaClient(
        YooKassaConfig(
            shop_id=settings.yookassa_shop_id,
            secret_key=settings.yookassa_secret_key.get_secret_value(),
            webhook_secret=settings.yookassa_webhook_secret.get_secret_value(),
            return_url_template=settings.yookassa_return_url_template,
            base_url=settings.yookassa_base_url,
        )
    )


async def _run_renewal() -> int:
    factory = get_session_factory()
    yookassa = _build_yookassa_from_env()
    email = EmailDispatcher(session_factory=factory)
    try:
        attempted = await renew_due_subscriptions(
            session_factory=factory,
            yookassa=yookassa,
            email=email,
        )
        log.info("cron_renewal_done", attempted=attempted)
        return 0
    finally:
        await yookassa.aclose()


async def _run_reminders() -> int:
    factory = get_session_factory()
    sent = await send_renewal_reminders(session_factory=factory)
    log.info("cron_reminders_done", sent=sent)
    return 0


async def _run_retry() -> int:
    factory = get_session_factory()
    yookassa = _build_yookassa_from_env()
    email = EmailDispatcher(session_factory=factory)
    try:
        retried = await retry_failed_renewals(
            session_factory=factory,
            yookassa=yookassa,
            email=email,
        )
        # Downgrade always runs after the retry sweep so a freshly-failed
        # 3rd attempt converts to PAYG in the same tick.
        downgraded = await downgrade_exhausted_subscriptions(
            session_factory=factory,
            email=email,
        )
        log.info("cron_retry_done", retried=retried, downgraded=downgraded)
        return 0
    finally:
        await yookassa.aclose()


async def _main(job: str) -> int:
    if job == "renewal":
        return await _run_renewal()
    if job == "reminders":
        return await _run_reminders()
    if job == "retry":
        return await _run_retry()
    log.error("cron_unknown_job", job=job)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="cron_renewal",
        description="Subscription Phase 2 cron jobs (renewal/reminders/retry).",
    )
    parser.add_argument(
        "--job",
        choices=("renewal", "reminders", "retry"),
        required=True,
        help="Which cron job to run.",
    )
    args = parser.parse_args()
    return asyncio.run(_main(args.job))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
