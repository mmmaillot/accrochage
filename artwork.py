"""Validate collection recipes, render 4K artwork, and generate evidence for review."""

import hashlib
import io
import json
import math
import re
import shutil
import struct
import tempfile
import unicodedata
from pathlib import Path

from PIL import Image, ImageCms, ImageDraw, ImageFont, ImageOps

import sources

WIDTH, HEIGHT = 3840, 2160
PROFILE = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))
_profile_bytes = PROFILE.tobytes()
# The ICC creation date should not change the fingerprint of an identical render.
PROFILE_BYTES = _profile_bytes[:24] + struct.pack(">6H", 2000, 1, 1, 0, 0, 0) + _profile_bytes[36:]
REQUIRED = ("title", "author", "date", "source", "license", "license_url", "credit", "rights_notes")
FORMATS = {"JPEG", "PNG", "WEBP", "TIFF"}
Image.MAX_IMAGE_PIXELS = 100_000_000


def json_text(data):
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def scoped_path(root, relative):
    requested = Path(relative)
    if requested.is_absolute() or ".." in requested.parts:
        raise ValueError(f"Expected a path inside {root}: {relative}")
    path = (root / requested).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError(f"File is outside {root}: {relative}")
    return path


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def validated_recipe(recipe):
    if (not isinstance(recipe, dict) or recipe.get("version") != 1
            or not isinstance(recipe.get("title"), str) or not recipe["title"].strip()
            or not isinstance(recipe.get("theme"), str) or not recipe["theme"].strip()):
        raise ValueError("Expected a version 1 recipe with a title and a theme")
    works = recipe.get("artworks")
    if not isinstance(works, list) or not 1 <= len(works) <= 100:
        raise ValueError("A recipe must contain 1 to 100 artworks")
    seen = set()
    for number, work in enumerate(works, 1):
        try:
            if not isinstance(work, dict):
                raise ValueError("Expected a record")
            for key in REQUIRED:
                if not isinstance(work.get(key), str) or not work[key].strip():
                    raise ValueError(f"Missing {key}")
            sources.checked_url(work["source"])
            sources.checked_url(work["license_url"])
            if work["source"] in seen:
                raise ValueError("Duplicate source page")
            seen.add(work["source"])
            if work.get("use") not in ("personal", "redistributable"):
                raise ValueError("use must be personal or redistributable")
            original = work.get("local_file")
            original_url = work.get("image_url")
            if bool(original) == bool(original_url):
                raise ValueError("Specify exactly one of local_file or image_url")
            if original:
                local = Path(original)
                if local.is_absolute() or ".." in local.parts:
                    raise ValueError("local_file must be relative to the recipe")
            else:
                sources.checked_url(original_url, sources.IMAGE_HOSTS)
            position = work.get("crop", {})
            if not isinstance(position, dict) or position.keys() - {"x", "y", "zoom", "trim"}:
                raise ValueError("Invalid crop fields")
            for axis in ("x", "y", "zoom"):
                if axis in position and (type(position[axis]) not in (int, float)
                                         or not 0 <= position[axis] <= 1):
                    raise ValueError(f"crop.{axis} must be between 0 and 1")
            if "trim" in position:
                trim = position["trim"]
                if (not isinstance(trim, list) or len(trim) != 4
                        or any(type(value) is not int or value < 0 for value in trim)
                        or trim[0] >= trim[2] or trim[1] >= trim[3]):
                    raise ValueError("crop.trim must be [left, top, right, bottom] in original pixels")
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Artwork {number}: {exc}") from exc
    return recipe


def crop_box(size, position):
    left, top, right, bottom = position.get("trim", [0, 0, *size])
    if right > size[0] or bottom > size[1]:
        raise ValueError(f"Trim exceeds the oriented source size {size}")
    usable_w, usable_h = right - left, bottom - top
    units = min(usable_w // 16, usable_h // 9)
    minimum = WIDTH // 16
    if units < minimum:
        raise ValueError(f"Source {usable_w} × {usable_h} is too small for 4K without upscaling")
    units -= round((units - minimum) * position.get("zoom", 0))
    w, h = units * 16, units * 9
    x = left + round((usable_w - w) * position.get("x", 0.5))
    y = top + round((usable_h - h) * position.get("y", 0.5))
    return x, y, x + w, y + h


def render(original, dest, position):
    with Image.open(original, formats=sorted(FORMATS)) as source:
        if source.width * source.height > Image.MAX_IMAGE_PIXELS:
            raise ValueError("Source exceeds 100 megapixels")
        image = ImageOps.exif_transpose(source)
        crop = crop_box(image.size, position)
        cut = image.crop(crop)
        icc = cut.info.get("icc_profile")
        if "A" in cut.getbands() or "transparency" in cut.info:
            rgba = cut.convert("RGBA")
            cut = Image.new("RGB", rgba.size, "black")
            cut.paste(rgba, mask=rgba.getchannel("A"))
        elif cut.mode not in ("L", "RGB", "CMYK", "LAB"):
            cut = cut.convert("RGB")
        if icc:
            cut = ImageCms.profileToProfile(cut, ImageCms.ImageCmsProfile(io.BytesIO(icc)), PROFILE, outputMode="RGB")
        elif cut.mode in ("CMYK", "LAB"):
            raise ValueError(f"Cannot convert an untagged {cut.mode} original to sRGB")
        else:
            cut = cut.convert("RGB")
        out = cut.resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)
        out.save(dest, "JPEG", quality=95, subsampling=0, icc_profile=PROFILE_BYTES)
        return list(image.size), list(crop)


def validate_jpeg(path):
    with Image.open(path) as image:
        if image.format != "JPEG" or image.size != (WIDTH, HEIGHT) or image.mode != "RGB":
            raise ValueError(f"{path.name} must be an RGB JPEG {WIDTH} × {HEIGHT}")
        profile = image.info.get("icc_profile")
        if not profile or "srgb" not in ImageCms.getProfileDescription(
                ImageCms.ImageCmsProfile(io.BytesIO(profile))).lower():
            raise ValueError(f"Missing sRGB profile in {path.name}")
        image.load()


def sheet_font(size):
    for name in (
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "DejaVuSans.ttf",
    ):
        try:
            return ImageFont.truetype(name, size), True
        except OSError:
            continue
    return ImageFont.load_default(size=size), False


def sheet_label(draw, position, text, font_info, available):
    font, unicode_support = font_info
    text = " ".join(text.split())
    if not unicode_support:
        text = "".join(char for char in unicodedata.normalize("NFKD", text)
                       if not unicodedata.combining(char)).encode("ascii", "replace").decode("ascii")
    if draw.textlength(text, font=font) > available:
        text = text[:160]
        while text and draw.textlength(text + "...", font=font) > available:
            text = text[:-1]
        text = text.rstrip() + "..."
    draw.text(position, text, font=font, fill="#202c25")


def render_sheets(folder, works, title):
    small = sheet_font(18)
    large = sheet_font(30)
    for start in range(0, len(works), 20):
        group = works[start:start + 20]
        cols = min(len(group), 4)
        sheet = Image.new("RGB", (20 + cols * 300, 74 + math.ceil(len(group) / cols) * 208), "#f5f0e7")
        draw = ImageDraw.Draw(sheet)
        sheet_label(draw, (16, 14), title, large, sheet.width - 32)
        for index, work in enumerate(group):
            x, y = 16 + index % cols * 300, 62 + index // cols * 208
            with Image.open(folder / work["file"]) as image:
                image.thumbnail((280, 158), Image.Resampling.LANCZOS)
                sheet.paste(image, (x, y))
            caption = f"{start + index + 1:02d}  {work['title']}"
            sheet_label(draw, (x, y + 165), caption, small, 280)
        sheet.save(folder / f"preview-{1 + start // 20}.jpg", quality=92, icc_profile=PROFILE_BYTES)


def prepare(recipe_path, output):
    recipe = validated_recipe(json.loads(recipe_path.read_text(encoding="utf-8")))
    if output.exists():
        raise ValueError(f"Output already exists: {output}. Use a new path for the revised selection")
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".accrochage-", dir=output.parent))
    try:
        (stage / "images").mkdir()
        records = []
        for number, work in enumerate(recipe["artworks"], 1):
            destination = stage / "images" / f"{number:02d}.jpg"
            temporary = stage / f"{number:02d}.original"
            try:
                if "local_file" in work and work["local_file"]:
                    original = scoped_path(recipe_path.parent, work["local_file"])
                else:
                    original = temporary
                    sources.download(work["image_url"], original)
                original_size, crop = render(original, destination, work.get("crop", {}))
                validate_jpeg(destination)
            except (ValueError, OSError, ImageCms.PyCMSError, Image.DecompressionBombError) as exc:
                raise ValueError(f"{number:02d}. {work['title']}: {exc}") from exc
            finally:
                temporary.unlink(missing_ok=True)
            records.append({
                **work, "file": destination.relative_to(stage).as_posix(), "sha256": digest(destination),
                "original_size": original_size, "crop_pixels": crop,
                "adaptation": "Full-screen 16:9 crop, sRGB conversion and 3840 × 2160 JPEG; no upscaling.",
            })
            print(f"Prepared {number:02d}. {work['title']} ({original_size[0]} × {original_size[1]})", flush=True)
        if len({work["sha256"] for work in records}) != len(records):
            raise ValueError("Duplicate rendered images in the collection")
        render_sheets(stage, records, recipe["title"])
        (stage / "report.json").write_text(json_text({**recipe, "artworks": records}), encoding="utf-8")
        credits = [recipe["title"], recipe["theme"], ""]
        for work in records:
            credits.extend([work["file"], *(f"{key}: {work[key]}" for key in REQUIRED), work["adaptation"], ""])
        (stage / "credits.txt").write_text("\n".join(credits), encoding="utf-8")
        stage.rename(output)
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def checked_output(folder):
    report = validated_recipe(json.loads((folder / "report.json").read_text(encoding="utf-8")))
    files = []
    for work in report["artworks"]:
        if not re.fullmatch(r"images/[0-9]{2,3}\.jpg", work.get("file", "")):
            raise ValueError("Unexpected filename in the export report")
        path = scoped_path(folder, work["file"])
        if digest(path) != work.get("sha256"):
            raise ValueError(f"{path.name} was changed after preparation")
        validate_jpeg(path)
        files.append((path, work["sha256"], work["title"]))
    if len({item[1] for item in files}) != len(files):
        raise ValueError("Duplicate rendered files in the report")
    return files
