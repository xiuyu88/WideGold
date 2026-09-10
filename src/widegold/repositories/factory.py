from functools import lru_cache

from widegold.repositories.memory import snapshot_repository
from widegold.settings.app import get_settings


@lru_cache(maxsize=1)
def repository():
    if get_settings().persistence.lower() == "postgres":
        from widegold.repositories.postgres import PostgresRepository
        return PostgresRepository()
    return snapshot_repository
