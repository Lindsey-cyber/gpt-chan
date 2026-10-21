"""Bound untrusted image responses before decompression and pixel decoding."""
import io
import zipfile

from PIL import Image

MAX_ARCHIVE_BYTES = 8 * 1024 * 1024
MAX_PNG_BYTES = 16 * 1024 * 1024
MAX_ARCHIVE_EXPANDED_BYTES = 32 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 8
MAX_COMPRESSION_RATIO = 200
MAX_IMAGE_PIXELS = 4 * 1024 * 1024
MAX_IMAGE_DIMENSION = 4096


def validate_png(data: bytes) -> bytes:
    if len(data) > MAX_PNG_BYTES:
        raise ValueError('Image exceeds byte limit')
    with Image.open(io.BytesIO(data)) as image:
        if image.format != 'PNG':
            raise ValueError('Expected PNG image')
        width, height = image.size
        if (width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION
                or width * height > MAX_IMAGE_PIXELS):
            raise ValueError('Image exceeds pixel limit')
        if getattr(image, 'n_frames', 1) != 1:
            raise ValueError('Expected one static image')
        image.verify()
    # verify checks container integrity; load also checks the compressed pixels.
    with Image.open(io.BytesIO(data)) as image:
        image.load()
    return data


def unpack_png(data: bytes) -> bytes:
    if len(data) > MAX_ARCHIVE_BYTES:
        raise ValueError('Archive exceeds byte limit')
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        if len(entries) > MAX_ARCHIVE_ENTRIES:
            raise ValueError('Archive has too many entries')
        if sum(entry.file_size for entry in entries) > MAX_ARCHIVE_EXPANDED_BYTES:
            raise ValueError('Archive exceeds expanded byte limit')
        for entry in entries:
            if entry.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                raise ValueError('Unsupported archive compression method')
            if entry.flag_bits & 1:
                raise ValueError('Encrypted archive is unsupported')
            if entry.file_size > max(1, entry.compress_size) * MAX_COMPRESSION_RATIO:
                raise ValueError('Archive compression ratio exceeds limit')
        images = [entry for entry in entries
                  if not entry.is_dir() and entry.filename.lower().endswith('.png')]
        if len(images) != 1:
            raise ValueError('Expected exactly one PNG in archive')
        entry = images[0]
        if entry.file_size > MAX_PNG_BYTES:
            raise ValueError('Image exceeds byte limit')
        # Also bound the actual read, rather than trusting only ZIP metadata.
        with archive.open(entry) as source:
            png = source.read(MAX_PNG_BYTES + 1)
        return validate_png(png)
