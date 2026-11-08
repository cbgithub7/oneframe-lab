"""Spec 005: the photo its CI and benches read is made upright, at four sizes, with no metadata."""

from __future__ import annotations

from pathlib import Path

from models import photos
from PIL import ExifTags, Image

METADATA = ("exif", "icc_profile", "xmp", "XML:com.adobe.xmp", "comment")


def a_phone_photo(path: Path) -> Path:
    """A 2048 x 1536 photo stored on its side (orientation 6: seen turned a quarter clockwise, so
    1536 wide and 2048 high), with a camera model and a place in its EXIF."""
    exif = Image.Exif()
    exif[ExifTags.Base.Orientation] = 6
    exif[ExifTags.Base.Model] = "Test phone"
    gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
    gps[ExifTags.GPS.GPSLatitudeRef] = "N"
    gps[ExifTags.GPS.GPSLatitude] = (52.0, 22.0, 1.0)
    Image.new("RGB", (2048, 1536), (90, 120, 150)).save(path, "JPEG", exif=exif)
    with Image.open(path) as stored:
        assert stored.getexif().get_ifd(ExifTags.IFD.GPSInfo)  # the place is there to be removed
    return path


def test_the_photos_are_upright_sized_and_carry_no_metadata(tmp_path: Path) -> None:
    original = a_phone_photo(tmp_path / "original.jpg")
    written = photos.write(original, tmp_path / "out")
    sizes = {}
    for path in written:
        with Image.open(path) as image:
            sizes[path.name] = image.size
            assert image.mode == "RGB"
            assert not image.getexif(), f"{path.name} carries EXIF"
            assert not [key for key in METADATA if key in image.info], f"{path.name} carries metadata"
    assert sizes == {
        "photo-full.jpg": (1536, 2048),
        "photo-1536.jpg": (1152, 1536),
        "photo-768.jpg": (576, 768),
        "photo-512.jpg": (384, 512),
    }
    assert original.read_bytes()  # only read: still there
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == sorted(sizes)  # no temporaries left
