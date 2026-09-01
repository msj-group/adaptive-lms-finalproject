"""Small, generated binary fixtures for M12 file-validation / storage /
serving tests. Nothing here reads a real file from disk -- every sample
is built in memory so tests never depend on (or risk touching) any real
asset.
"""

import io
import struct
import zipfile

# ---------------------------------------------------------------------------
# Minimal *valid* samples -- one per supported extension, plus a few
# deliberately-invalid variants used across the negative test matrix.
# ---------------------------------------------------------------------------


def minimal_pdf():
    return b"%PDF-1.4\n1 0 obj<< >>\nendobj\ntrailer<< >>\n%%EOF"


def minimal_docx(extra_entries=None):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("_rels/.rels", "<Relationships/>")
        archive.writestr("word/document.xml", "<w:document><w:body/></w:document>")
        for name, content in (extra_entries or {}).items():
            archive.writestr(name, content)
    return buf.getvalue()


def docx_with_macro():
    return minimal_docx({"word/vbaProject.bin": b"\x00" * 32})


def docx_missing_required_entry():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("_rels/.rels", "<Relationships/>")
        # deliberately omit word/document.xml
    return buf.getvalue()


def docx_with_traversal_entry():
    return minimal_docx({"../../etc/passwd": "x"})


def docx_with_absolute_entry():
    return minimal_docx({"/etc/passwd": "x"})


def docx_aggregate_bomb(entry_count=12, entry_uncompressed_bytes=400):
    """A DOCX whose members are each individually tiny (well under any
    per-entry cap) but whose *aggregate* uncompressed size is large. Used
    with a monkeypatched aggregate cap so the test never allocates real
    hundreds of megabytes -- ``entry_count * entry_uncompressed_bytes``
    stays a few KiB here."""
    extra = {
        f"word/filler{i}.xml": "A" * entry_uncompressed_bytes for i in range(entry_count)
    }
    return minimal_docx(extra)


def docx_with_encrypted_entry():
    """A structurally valid DOCX whose ZIP entries carry the
    general-purpose "encrypted" bit (flag bit 0). Opens fine for
    metadata inspection; ``inspect_docx_zip`` must reject it before
    anyone tries to read an entry."""
    data = bytearray(minimal_docx({"word/secret.xml": "s" * 32}))
    for signature, flag_offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        pos = 0
        while True:
            found = data.find(signature, pos)
            if found < 0:
                break
            data[found + flag_offset] |= 0x01
            pos = found + 4
    return bytes(data)


def docx_with_zero_compress_size():
    """A structurally valid DOCX where one non-empty member's central
    directory reports ``compress_size == 0`` (physically impossible --
    a crafted value). ``inspect_docx_zip`` must reject it."""
    data = bytearray(minimal_docx({"word/extra.xml": b"x" * 80}))
    pos = 0
    while True:
        found = data.find(b"PK\x01\x02", pos)
        if found < 0:
            break
        name_len = struct.unpack_from("<H", data, found + 28)[0]
        name = data[found + 46 : found + 46 + name_len].decode("ascii", "replace")
        if name == "word/extra.xml":
            struct.pack_into("<I", data, found + 20, 0)  # compress_size -> 0
        pos = found + 4
    return bytes(data)


def generic_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("readme.txt", "just a plain zip, not a docx")
    return buf.getvalue()


def legacy_doc():
    # OLE Compound File Binary Format signature -- not a ZIP at all.
    return b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 40


def minimal_png():
    # 1x1 transparent PNG.
    return (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
        b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )


def minimal_jpeg():
    return b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"


def minimal_gif():
    return b"GIF89a" + b"\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"


def minimal_webp():
    payload = b"VP8 " + b"\x00" * 16
    size = (4 + len(payload)).to_bytes(4, "little")
    return b"RIFF" + size + b"WEBP" + payload


def minimal_mp3():
    # ID3v2 header (10 bytes) followed by a couple of padding bytes.
    return b"ID3\x03\x00\x00\x00\x00\x00\x00" + b"\x00" * 16


def minimal_mp3_frame_sync():
    return b"\xff\xfb\x90\x00" + b"\x00" * 32


def minimal_wav():
    payload = b"fmt " + (16).to_bytes(4, "little") + b"\x00" * 16 + b"data" + (0).to_bytes(4, "little")
    size = (4 + len(payload)).to_bytes(4, "little")
    return b"RIFF" + size + b"WAVE" + payload


def minimal_mp4():
    box = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2mp41"
    return box


def minimal_webm():
    return b"\x1a\x45\xdf\xa3" + b"\x00" * 32


def not_a_real_format(extension):
    """Bytes that will fail the signature check for `extension` -- looks
    nothing like the real format."""
    return b"this is not a " + extension.encode() + b" file, just plain text padding" * 4


def executable_bytes():
    # Windows PE / DOS stub signature.
    return b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff\x00\x00" + b"\x00" * 32


def svg_bytes():
    return b"<?xml version='1.0'?><svg xmlns='http://www.w3.org/2000/svg'></svg>"


def html_bytes():
    return b"<!doctype html><html><body><script>alert(1)</script></body></html>"


MINIMAL_VALID_BY_EXTENSION = {
    "pdf": minimal_pdf,
    "docx": minimal_docx,
    "png": minimal_png,
    "jpg": minimal_jpeg,
    "jpeg": minimal_jpeg,
    "gif": minimal_gif,
    "webp": minimal_webp,
    "mp3": minimal_mp3,
    "wav": minimal_wav,
    "mp4": minimal_mp4,
    "webm": minimal_webm,
}

CANONICAL_MIME_BY_EXTENSION = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "mp4": "video/mp4",
    "webm": "video/webm",
}
