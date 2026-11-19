"""Image upload handling shared by profile photos, posts and chat.

Every upload is re-encoded with Pillow: this validates it really is an image,
fixes phone rotation, caps the size and strips EXIF metadata (GPS location!).
"""
import uuid
from io import BytesIO

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.http import FileResponse, Http404
from PIL import Image, ImageOps, UnidentifiedImageError

Image.MAX_IMAGE_PIXELS = 40_000_000


def process_image(upload, max_side=1600):
    if upload.size > settings.MAX_IMAGE_UPLOAD_MB * 1024 * 1024:
        raise ValidationError(f"Images must be under {settings.MAX_IMAGE_UPLOAD_MB} MB.")
    try:
        img = Image.open(upload)
        img.load()
        img = ImageOps.exif_transpose(img)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ValidationError("That file isn't an image we can read.")
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        background = Image.new("RGB", img.size, (255, 255, 255))
        background.paste(img, mask=img.split()[-1])
        img = background
    elif img.mode != "RGB":
        img = img.convert("RGB")
    img.thumbnail((max_side, max_side))
    buf = BytesIO()
    img.save(buf, "JPEG", quality=85, optimize=True)
    return ContentFile(buf.getvalue(), name=f"{uuid.uuid4().hex}.jpg")


def serve_image(field_file):
    if not field_file:
        raise Http404
    try:
        handle = field_file.open("rb")
    except (FileNotFoundError, OSError):
        raise Http404
    response = FileResponse(handle, content_type="image/jpeg")
    response["Cache-Control"] = "private, max-age=3600"
    return response
