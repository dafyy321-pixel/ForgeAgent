"""Provider admission shared by workers connected to the same PostgreSQL cluster."""

import asyncio
from contextlib import asynccontextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from .config import settings
from .domain import digest

admission_engine = create_engine(settings.database_url, poolclass=NullPool)


def provider_key():
    return "provider:" + digest([settings.model_provider, settings.model_base_url, settings.model_id])


def acquire(resource, limit):
    # Long requests must not consume the pool needed for heartbeat and commit transactions.
    connection = admission_engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    try:
        for slot in range(limit):
            key = f"forge:admission:{resource}:{slot}"
            acquired = connection.scalar(text("SELECT pg_try_advisory_lock(hashtextextended(:key,0))"), {"key": key})
            if acquired:
                return connection, key
    except BaseException:
        connection.close()
        raise
    connection.close()
    return None


def acquire_provider():
    return acquire(provider_key(), settings.provider_concurrency)


def release_provider(lease):
    connection, key = lease
    try:
        connection.execute(text("SELECT pg_advisory_unlock(hashtextextended(:key,0))"), {"key": key})
    finally:
        connection.close()


@asynccontextmanager
async def admission(resource, limit):
    acquisition = asyncio.create_task(asyncio.to_thread(acquire, resource, limit))
    try:
        lease = await asyncio.shield(acquisition)
    except asyncio.CancelledError:
        lease = await acquisition
        if lease:
            await asyncio.to_thread(release_provider, lease)
        raise
    try:
        yield bool(lease)
    finally:
        if lease:
            await asyncio.shield(asyncio.to_thread(release_provider, lease))
