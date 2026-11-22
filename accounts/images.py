"""Image upload handling shared by profile photos, posts and chat.

Every upload is re-encoded with Pillow: this validates it really is an image,
fixes phone rotation, caps the size and strips EXIF metadata (GPS location!).

The one exception is an animated GIF in a chat (allow_gif): re-encoding those is slow and makes them
bigger, so the GIF is rebuilt block by block instead, keeping only its frames, their timing and the
loop setting, and dropping comments and any other embedded data. Pillow then has to decode every frame.
"""
import mimetypes
import uuid
from io import BytesIO

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.http import FileResponse, Http404
from PIL import Image, ImageOps, ImageSequence, UnidentifiedImageError

Image.MAX_IMAGE_PIXELS = 40_000_000

GIF_MAX_SIDE = 1000              # keyboard GIFs are about 500 pixels wide
GIF_MAX_FRAMES = 300
GIF_MAX_PIXELS = 100_000_000     # width x height x frames: what the other phone has to draw


def process_image(upload, max_side=1600, allow_gif=False):
    if upload.size > settings.MAX_IMAGE_UPLOAD_MB * 1024 * 1024:
        raise ValidationError(f"Images must be under {settings.MAX_IMAGE_UPLOAD_MB} MB.")
    if allow_gif:
        upload.seek(0)
        if upload.read(6) in (b"GIF87a", b"GIF89a"):
            upload.seek(0)
            gif = clean_gif(upload.read())
            if gif is not None:
                return ContentFile(gif, name=f"{uuid.uuid4().hex}.gif")
        upload.seek(0)   # not animated: a still picture like any other
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


def clean_gif(data):
    """The GIF rebuilt with only what's needed to play it, or None if it has a single frame (then it's
    handled like any photo). Raises ValidationError for anything that isn't a well-formed GIF."""
    bad = ValidationError("That GIF couldn't be read.")
    if len(data) < 13 or data[:6] not in (b"GIF87a", b"GIF89a"):
        raise bad
    width, height = int.from_bytes(data[6:8], "little"), int.from_bytes(data[8:10], "little")
    if not (0 < width <= GIF_MAX_SIDE and 0 < height <= GIF_MAX_SIDE):
        raise ValidationError(f"GIFs can be at most {GIF_MAX_SIDE} pixels wide and high.")

    def colour_table(packed):
        return 3 * (2 << (packed & 7)) if packed & 0x80 else 0

    def after_sub_blocks(pos):   # data comes in blocks of up to 255 bytes, ended by an empty one
        while True:
            if pos >= len(data):
                raise bad
            size = data[pos]
            pos += 1 + size
            if size == 0:
                return pos

    pos = 13 + colour_table(data[10])            # header, screen size, global colours
    out = bytearray(data[:pos])
    frames = 0
    while True:
        if pos >= len(data):
            raise bad                            # cut off before the end marker
        block = data[pos]
        if block == 0x3B:                        # the end; anything hidden after it is dropped
            out.append(0x3B)
            break
        if block == 0x21:                        # an extension
            if pos + 2 > len(data):
                raise bad
            label, end = data[pos + 1], after_sub_blocks(pos + 2)
            keep = (label == 0xF9                # frame timing and transparency
                    or (label == 0xFF and data[pos + 2] == 11 and data[pos + 3:pos + 14] in (b"NETSCAPE2.0", b"ANIMEXTS1.0")))
            if keep:                             # ... and the loop setting; comments, XMP and the rest go
                out += data[pos:end]
            pos = end
        elif block == 0x2C:                      # a frame
            if pos + 10 > len(data):
                raise bad
            start_of_pixels = pos + 10 + colour_table(data[pos + 9])
            end = after_sub_blocks(start_of_pixels + 1)
            out += data[pos:end]
            pos = end
            frames += 1
            if frames > GIF_MAX_FRAMES:
                raise ValidationError(f"GIFs can have at most {GIF_MAX_FRAMES} frames.")
        else:
            raise bad
    if frames == 0:
        raise bad
    if frames == 1:
        return None
    if width * height * frames > GIF_MAX_PIXELS:
        raise ValidationError("That GIF is too big. Try a shorter or smaller one.")
    try:                                         # it has to be a real GIF all the way through
        for frame in ImageSequence.Iterator(Image.open(BytesIO(bytes(out)))):
            frame.load()
    except (UnidentifiedImageError, OSError, ValueError, EOFError, Image.DecompressionBombError):
        raise bad
    return bytes(out)


def serve_image(field_file):
    if not field_file:
        raise Http404
    try:
        handle = field_file.open("rb")
    except (FileNotFoundError, OSError):
        raise Http404
    kind = mimetypes.guess_type(field_file.name)[0]
    response = FileResponse(handle, content_type=kind if kind in ("image/gif", "image/png") else "image/jpeg")
    response["Cache-Control"] = "private, max-age=3600"
    return response
