from celery import Celery
from celery.schedules import crontab

from app.config import settings

celery_app = Celery(
    "openclaw",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    # Task routing: high-priority queue for Layer 0 audit
    task_routes={
        "openclaw.settle_task": {"queue": "settlement_queue"},
        "openclaw.assign_onboarding_tasks": {"queue": "celery"},
        "openclaw.coldstart_publisher": {"queue": "celery"},
        "openclaw.release_expired_claims": {"queue": "celery"},
    },
    task_queues_max_priority=10,
    task_default_priority=5,
    # Beat schedule (TASK-014: claim release, TASK-021: cold-start)
    beat_schedule={
        # TASK-014: release expired claim locks every 2 minutes
        "release-expired-claims": {
            "task": "openclaw.release_expired_claims",
            "schedule": 120,  # seconds
        },
        # TASK-021: cold-start publisher every 5 minutes
        "coldstart-publisher": {
            "task": "openclaw.coldstart_publisher",
            "schedule": 300,
        },
        # TASK-024: monthly REP decay – 1st of every month at 03:00 UTC
        "monthly-rep-decay": {
            "task": "openclaw.monthly_rep_decay",
            "schedule": crontab(day_of_month=1, hour=3, minute=0),
        },
        # TASK-029: publish daily benchmark tasks at 08:00 UTC
        "daily-benchmarks": {
            "task": "openclaw.publish_benchmarks",
            "schedule": crontab(hour=8, minute=0),
        },
    },
)

# Auto-discover tasks from workers package
celery_app.autodiscover_tasks(["app.workers"])
