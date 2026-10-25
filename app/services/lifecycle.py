from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.source import Source
from app.models.collection import Collection
from app.services.object_store import get_object_store, source_prefix


def delete_source_and_local_media(db: Session, source: Source) -> None:
    """Delete a source record and only media paths provably below MEDIA_ROOT.

    Its collection memberships go with it, so it disappears from every collection at once.
    """
    media_root = Path(get_settings().media_root).resolve()
    for path_string in [source.media_path, *(asset.path for asset in source.media_assets)]:
        if not path_string:
            continue
        path = Path(path_string).resolve()
        if path.is_relative_to(media_root):
            path.unlink(missing_ok=True)
    for working_dir in (media_root / "prepared" / str(source.id), media_root / "fetched" / str(source.id)):
        if working_dir.is_dir():
            for child in sorted(working_dir.rglob("*"), reverse=True):
                if child.is_file():
                    child.unlink(missing_ok=True)
                elif child.is_dir():
                    child.rmdir()
            working_dir.rmdir()
    _remove_stored_objects(source)
    db.delete(source)
    db.commit()


def delete_collection(db: Session, collection: Collection) -> None:
    """Delete a collection. Sources live in the library, so no media is touched."""
    db.delete(collection)
    db.commit()


def _remove_stored_objects(source: Source) -> None:
    """Best effort: a storage backend outage must not block deleting the user's own data."""
    if not source.stored_objects:
        return
    try:
        get_object_store().delete_prefix(source_prefix(source.user_id, source.id))
    except Exception:
        pass
