from io import BytesIO

from PIL import Image, ImageOps

MAX_BYTES = 2 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 40_000_000
_ALLOWED = {"PNG", "JPEG", "WEBP"}


def reencode_avatar(data: bytes) -> bytes:
    if not data or len(data) > MAX_BYTES:
        raise ValueError("size")
    try:
        with Image.open(BytesIO(data)) as im:
            if im.format not in _ALLOWED:
                raise ValueError("format")
            im = ImageOps.exif_transpose(im)
            im = ImageOps.fit(im.convert("RGB"), (256, 256), Image.LANCZOS)
            out = BytesIO()
            im.save(out, "WEBP", quality=85)  # fresh image: no EXIF/ICC carried over
            return out.getvalue()
    except (Image.DecompressionBombError, Image.DecompressionBombWarning, OSError, SyntaxError) as e:
        raise ValueError("decode") from e
