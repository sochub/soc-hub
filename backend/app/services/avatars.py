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
            if im.size[0] * im.size[1] > Image.MAX_IMAGE_PIXELS:  # before any decode
                raise ValueError("size")
            if im.format == "JPEG":
                im.draft("RGB", (512, 512))  # decode at reduced scale (DCT), bounding memory
            im = ImageOps.exif_transpose(im)
            im.thumbnail((1024, 1024))  # shrink before the full-size RGBA copy
            rgba = im.convert("RGBA")
            bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
            rgb = Image.alpha_composite(bg, rgba).convert("RGB")
            rgb = ImageOps.fit(rgb, (256, 256), Image.LANCZOS)
            out = BytesIO()
            rgb.save(out, "WEBP", quality=85)  # fresh image: no EXIF/ICC carried over
            return out.getvalue()
    except ValueError:
        raise
    except Exception as e:
        raise ValueError("decode") from e


def avatar_version(avatar_key):
    """Opaque cache-buster derived from the stored avatar key (None when no avatar)."""
    return avatar_key.rsplit('/', 1)[-1].split('.')[0][:16] if avatar_key else None
