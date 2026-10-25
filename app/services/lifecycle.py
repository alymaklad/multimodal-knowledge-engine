from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.reel import Reel
from app.models.collection import Collection
from app.services.object_store import get_object_store, reel_prefix


def delete_reel_and_local_media(db: Session, reel: Reel) -> None:
    """Delete a Reel record and only media paths provably below MEDIA_ROOT."""
    media_root = Path(get_settings().media_root).resolve()
    for path_string in [reel.media_path, *(asset.path for asset in reel.media_assets)]:
        if not path_string:
            continue
        path = Path(path_string).resolve()
        if path.is_relative_to(media_root):
            path.unlink(missing_ok=True)
    prepared_dir = media_root / "prepared" / str(reel.id)
    if prepared_dir.is_dir():
        for child in sorted(prepared_dir.rglob("*"), reverse=True):
            if child.is_file():
                child.unlink(missing_ok=True)
            elif child.is_dir():
                child.rmdir()
        prepared_dir.rmdir()
    _remove_stored_objects(reel)
    db.delete(reel)
    db.commit()


def delete_collection_and_local_media(db: Session, collection: Collection) -> None:
    """Delete a collection and files underneath the configured media root only."""
    for reel in list(collection.reels):
        _remove_local_media(reel)
        _remove_stored_objects(reel)
    db.delete(collection)
    db.commit()


def _remove_local_media(reel: Reel) -> None:
    media_root = Path(get_settings().media_root).resolve()
    for path_string in [reel.media_path, *(asset.path for asset in reel.media_assets)]:
        if path_string:
            path = Path(path_string).resolve()
            if path.is_relative_to(media_root):
                path.unlink(missing_ok=True)


def _remove_stored_objects(reel: Reel) -> None:
    """Best effort: a storage backend outage must not block deleting the user's own data."""
    if not reel.stored_objects:
        return
    try:
        get_object_store().delete_prefix(reel_prefix(reel.collection.user_id, reel.id))
    except Exception:
        pass
