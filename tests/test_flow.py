from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

import artwork
import sources
import television

RECIPE = Path(__file__).resolve().parents[1] / "collections" / "cleveland-winter.json"


class FakeArt:
    def __init__(self, lose_ack=False):
        self.ids = {"pre-existing"}
        self.uploads = 0
        self.lose_ack = lose_ack

    def available(self):
        return [{"content_id": identifier} for identifier in self.ids]

    def upload(self, data, matte, portrait_matte, file_type):
        assert data and matte == portrait_matte == "none" and file_type == "jpg"
        self.uploads += 1
        identifier = f"MY_F{self.uploads:04}"
        self.ids.add(identifier)
        if self.lose_ack:
            raise TimeoutError()
        return identifier


class CollectionFlow(unittest.TestCase):
    def test_example_recipe_and_local_target(self):
        recipe = json.loads(RECIPE.read_text())
        self.assertEqual(len(artwork.validated_recipe(recipe)["artworks"]), 3)
        recipe["artworks"][0]["image_url"] = "https://192.168.0.3/photo.jpg"
        with self.assertRaises(ValueError):
            artwork.validated_recipe(recipe)
        with self.assertRaises(ValueError):
            television.local_ip("8.8.8.8")
        self.assertEqual(television.local_ip("192.168.1.8"), "192.168.1.8")

    def test_crop_and_dimension_limit(self):
        position = {"trim": [50, 50, 6050, 3950], "zoom": 1, "x": 1, "y": 0}
        self.assertEqual(artwork.crop_box((6100, 4000), position), (2210, 50, 6050, 2210))
        with self.assertRaisesRegex(ValueError, "too small"):
            artwork.crop_box((3840, 2159), {})

    def test_render_integrity_and_resume_after_an_uncertain_upload(self):
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(io.StringIO()):
            root = Path(directory)
            Image.new("RGB", (4200, 2600), "#29445e").save(root / "original.png")
            recipe = json.loads(RECIPE.read_text())
            recipe["artworks"] = recipe["artworks"][:1]
            del recipe["artworks"][0]["image_url"]
            recipe["artworks"][0]["local_file"] = "original.png"
            path = root / "recipe.json"
            path.write_text(json.dumps(recipe))
            output = root / "output"
            artwork.prepare(path, output)
            files = artwork.checked_output(output)
            self.assertTrue((output / "preview-1.jpg").is_file())
            self.assertTrue((output / "credits.txt").is_file())

            state = {"receipts": {}, "pending": None}
            snapshots = []

            def save(value):
                snapshots.append(deepcopy(value))

            tv = FakeArt()
            television.add_images(tv, files, state, save)
            self.assertEqual(state["receipts"][files[0][1]], "MY_F0001")
            self.assertIsNone(state["pending"])

            state = {"receipts": {}, "pending": None}
            tv = FakeArt(lose_ack=True)
            with self.assertRaisesRegex(ValueError, "result uncertain"):
                television.add_images(tv, files, state, save)
            self.assertEqual(snapshots[-1]["pending"]["sha256"], files[0][1])
            state = snapshots[-1]
            with self.assertRaisesRegex(ValueError, "needs verification"):
                television.add_images(tv, files, state, save)
            television.add_images(tv, files, state, save, resolve_id="MY_F0001")
            television.add_images(tv, files, state, save)
            self.assertEqual(tv.uploads, 1)
            self.assertIn("pre-existing", tv.ids)

            rendered = files[0][0]
            rendered.write_bytes(rendered.read_bytes() + b"changed")
            with self.assertRaisesRegex(ValueError, "changed after preparation"):
                artwork.checked_output(output)

    def test_cleveland_checks_image_rights_independently_of_metadata(self):
        example = {
            "title": "Smoke", "creators": [{"description": "Artist"}], "creation_date": "1809",
            "url": "https://www.clevelandart.org/art/example", "accession_number": "1",
            "images": {"print": {"url": "https://openaccess-cdn.clevelandart.org/art.jpg"}},
        }
        items = [{**example, "share_license_status": rights} for rights in ("Copyrighted", "CC0")]
        with patch.object(sources, "api", return_value={"data": items}):
            results = list(sources.cleveland("smoke", 10))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["license"], "CC0 1.0")
