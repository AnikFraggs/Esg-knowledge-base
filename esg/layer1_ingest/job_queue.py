"""Layer 1: DB-backed job queue (ops basic).

Simple on purpose: one `jobs` table + a worker loop. Swap in Celery/RQ/Redis
when scaling - only this file and pipeline.run_pending() touch the queue.
"""
from datetime import datetime

from sqlalchemy import select

from esg.db import get_session
from esg.models import Job


def enqueue(kind: str, payload: dict) -> int:
    with get_session() as session:
        job = Job(kind=kind, payload=payload, status="pending")
        session.add(job)
        session.flush()
        return job.id


def next_pending(session) -> Job | None:
    return session.execute(
        select(Job).where(Job.status == "pending")
        .order_by(Job.id).limit(1)
    ).scalar_one_or_none()


def mark_running(session, job: Job):
    job.status = "running"
    job.updated_at = datetime.utcnow()
    session.commit()


def mark_done(session, job: Job):
    job.status = "done"
    job.updated_at = datetime.utcnow()
    session.commit()


def mark_error(session, job: Job, error: str):
    job.status = "error"
    job.error = str(error)[:2000]
    job.updated_at = datetime.utcnow()
    session.commit()


def recover_stuck_jobs(session) -> int:
    """Reset jobs that were left in 'running' state back to 'pending'."""
    stuck = list(session.execute(
        select(Job).where(Job.status == "running")
    ).scalars())
    for j in stuck:
        j.status = "pending"
        j.updated_at = datetime.utcnow()
    session.commit()
    return len(stuck)


class JobQueue:
    """Object-oriented wrapper over the SQLite-backed job queue for server endpoints."""

    def enqueue(self, kind: str, payload: dict) -> int:
        return enqueue(kind, payload)

    def process_pending(self, limit: int = 10) -> int:
        from esg import pipeline
        with get_session() as session:
            recover_stuck_jobs(session)
        return pipeline.run_pending(limit=limit)
