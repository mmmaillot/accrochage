# Local collection recipes

The file `work/<theme>/collection.json` records the selected artworks and their order. The agent can use it to rebuild the same collection or prepare a new set of crops. See [Winter in Cleveland](collections/cleveland-winter.json) for an example.

- The document contains `version` (1), `title`, `theme`, and an ordered `artworks` array.
- Each artwork has `title`, `author`, `date`, `source`, `license`, `license_url`, `credit`, `rights_notes`, and `use`. Set `use` to `personal` or `redistributable` based on the rights you verified. Set `source` to the artwork’s source record.
- Use `image_url` for the original on a supported host or `local_file` for an original stored at a path relative to the recipe's directory.
- The optional settings `crop.x`, `crop.y`, and `crop.zoom` range from 0 to 1. On the x and y axes, 0 aligns the crop with the left or top, 1 aligns it with the right or bottom, and 0.5 centers it. Zoom defaults to 0 and stops at a 3840 × 2160 crop without upscaling.
- The optional `crop.trim` defines a rectangle in the orientation-corrected original, in pixels `[left, top, right, bottom]`.

Run `.venv/bin/python frame.py check-recipe work/<theme>/collection.json` to validate the file format. Verify usage rights against the source records.
