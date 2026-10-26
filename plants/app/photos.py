"""Photo files for the Plants app.

Each photo is stored twice in the app's data folder (/data/photos/<plant>/):
  <id>.jpg    full size, long edge 1600 px (~300 KB)
  <id>_t.jpg  thumbnail for tiles and the photo strip, long edge 480 px
The ~1024 px image Home Assistant's camera entity shows is made on the fly.
"""

import io
import os
from datetime import datetime, timedelta

from PIL import Image, ImageOps, UnidentifiedImageError

from . import db

PHOTO_DIR = os.path.join(db.DATA_DIR, "photos")

FULL_EDGE, FULL_QUALITY = 1600, 82
THUMB_EDGE, THUMB_QUALITY = 480, 78
CAMERA_EDGE, CAMERA_QUALITY = 1024, 80
MAX_UPLOAD = 30 * 1024 * 1024

# Retention: every photo is kept for about 3 months, then one per plant per month.
KEEP_ALL_DAYS = 92

EXIF_IFD = 0x8769
DATETIME_ORIGINAL = 36867
DATETIME = 306


def full_path(number: int, photo_id: int) -> str:
    return os.path.join(PHOTO_DIR, str(number), f"{photo_id}.jpg")


def thumb_path(number: int, photo_id: int) -> str:
    return os.path.join(PHOTO_DIR, str(number), f"{photo_id}_t.jpg")


def _exif_taken_at(img) -> str | None:
    """The camera's own 'taken' time, if the file has one."""
    try:
        exif = img.getexif()
        raw = exif.get_ifd(EXIF_IFD).get(DATETIME_ORIGINAL) or exif.get(DATETIME)
        if not raw:
            return None
        when = datetime.strptime(str(raw).strip("\x00 ")[:19], "%Y:%m:%d %H:%M:%S").astimezone()
    except Exception:
        return None
    if when.year < 2000 or when > datetime.now().astimezone() + timedelta(days=1):
        return None
    return when.isoformat(timespec="seconds")


def _hint_taken_at(hint_ms) -> str | None:
    """The file's date from the phone (ms since 1970), used when there's no EXIF date."""
    try:
        when = datetime.fromtimestamp(int(hint_ms) / 1000).astimezone()
    except (TypeError, ValueError, OverflowError, OSError):
        return None
    if when.year < 2000 or when > datetime.now().astimezone() + timedelta(days=1):
        return None
    return when.isoformat(timespec="seconds")


def _save_jpeg(img, path: str, edge: int, quality: int) -> tuple[int, int, int]:
    copy = img.copy()
    copy.thumbnail((edge, edge), Image.Resampling.LANCZOS)
    tmp = path + ".tmp"
    copy.save(tmp, "JPEG", quality=quality, optimize=True, progressive=True)
    os.replace(tmp, path)
    return copy.width, copy.height, os.path.getsize(path)


def process_upload(raw: bytes, hint_ms=None) -> dict:
    """Read an uploaded image and return it ready to store (no files written yet)."""
    if not raw:
        raise db.UserError("The photo was empty.")
    if len(raw) > MAX_UPLOAD:
        raise db.UserError("That file is too big (over 30 MB).")
    try:
        img = Image.open(io.BytesIO(raw))
        taken_at = _exif_taken_at(img)
        # Decode JPEGs at a reduced scale: much less memory on the Pi.
        w, h = img.size
        scale = FULL_EDGE / max(w, h)
        if scale < 1:
            img.draft("RGB", (int(w * scale) + 1, int(h * scale) + 1))
        img = ImageOps.exif_transpose(img)
        img.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise db.UserError("Couldn't read that image. Use a JPEG or PNG (HEIC isn't supported).")
    if img.mode != "RGB":
        img = img.convert("RGB")
    return {
        "image": img,
        "taken_at": taken_at or _hint_taken_at(hint_ms) or db.now_iso(),
    }


def store(number: int, photo_id: int, img) -> tuple[int, int, int]:
    """Write the full-size and thumbnail files. Returns (width, height, bytes) of the full one."""
    os.makedirs(os.path.join(PHOTO_DIR, str(number)), exist_ok=True)
    w, h, size = _save_jpeg(img, full_path(number, photo_id), FULL_EDGE, FULL_QUALITY)
    _, _, tsize = _save_jpeg(img, thumb_path(number, photo_id), THUMB_EDGE, THUMB_QUALITY)
    return w, h, size + tsize


def camera_jpeg(number: int, photo_id: int) -> bytes:
    """The ~1024 px version for Home Assistant's camera entity."""
    with Image.open(full_path(number, photo_id)) as img:
        img.thumbnail((CAMERA_EDGE, CAMERA_EDGE), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        img.save(out, "JPEG", quality=CAMERA_QUALITY, optimize=True)
    return out.getvalue()


def remove_files(number: int, photo_id: int) -> None:
    for path in (full_path(number, photo_id), thumb_path(number, photo_id)):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass


def remove_plant_files(number: int) -> None:
    folder = os.path.join(PHOTO_DIR, str(number))
    if os.path.isdir(folder):
        for name in os.listdir(folder):
            try:
                os.remove(os.path.join(folder, name))
            except OSError:
                pass
        try:
            os.rmdir(folder)
        except OSError:
            pass


def apply_retention() -> int:
    """Thin out photos older than ~3 months to one per plant per month. Returns how many went."""
    cutoff = (datetime.now().astimezone() - timedelta(days=KEEP_ALL_DAYS)).isoformat(timespec="seconds")
    removed = 0
    with db.session() as con:
        for photo_id in db.photos_to_thin(con, cutoff):
            p = db.delete_photo(con, photo_id)
            remove_files(p["plant_number"], photo_id)
            removed += 1
    return removed
