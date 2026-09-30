"""
Redis: job queue, live status events, and login sessions.

  btdm:jobs                 list   — work items for the inference worker (BLPOP)
  btdm:study:<id>           pubsub — status events for one study (SSE stream)
  btdm:session:<sha256>     string — "<user id>|<issued-at>"; idle timeout (sliding),
                            absolute limit SESSION_TTL_HOURS
  btdm:worker:heartbeat     string — set by the worker every loop, 30 s TTL
"""
from __future__ import annotations

import json
from functools import lru_cache

import redis

from app.config import REDIS_URL

JOB_QUEUE = "btdm:jobs"
HEARTBEAT_KEY = "btdm:worker:heartbeat"
ACTIVE_MODEL_KEY = "btdm:worker:active_model"


@lru_cache(maxsize=1)
def client() -> redis.Redis:
    return redis.Redis.from_url(REDIS_URL, decode_responses=True)


def study_channel(study_id: str) -> str:
    return f"btdm:study:{study_id}"


def enqueue(job: dict) -> None:
    client().rpush(JOB_QUEUE, json.dumps(job))


def publish_study(study_id: str, **event) -> None:
    event.setdefault("studyId", study_id)
    client().publish(study_channel(study_id), json.dumps(event))
