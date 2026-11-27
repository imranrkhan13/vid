"""Background task execution (section 5).

Default is an in-process asyncio runner so the harness needs no Redis/Celery.
A Celery-backed runner is wired in ``celery_runner`` and only imported when a
broker is configured, so Celery stays an optional dependency.
"""
