# Accrochage

Clone this repository on your laptop, open the folder in your agent, and ask for a collection for your Samsung The Frame.

> Create a collection about the Mediterranean in winter, with a mix of photographs and paintings.

The agent finds existing artworks online, checks their rights and framing, prepares the 4K JPEGs, and adds them to **Art Mode → My Photos** on your TV. Your collections live in the local `work/` folder, which Git ignores.

## First use

1. Open the cloned folder in an agent that can read [AGENTS.md](AGENTS.md), search the web, inspect images, and run commands on your macOS or Linux laptop. Install Python 3.12 or 3.13 if needed. The agent sets up the remaining dependencies in the clone.
2. Put the TV in Art Mode and connect your laptop to the same local network. For first-time pairing, give the agent the private IP address shown in the TV's network settings and accept the Accrochage prompt if it appears.
3. Ask for a theme in natural language. Once the TV is paired, “Create a collection on this theme” starts the research and upload. Add “preview only” if you want to review the collection first.

The agent gives you the contact sheet, credits, and upload result for each collection. It adds images to **My Photos** in the order it prepared them. Grouping and slideshow order on the TV depend on the model and firmware.

## Manual use

The [Winter in Cleveland example](collections/cleveland-winter.json) downloads about 415 MiB of original TIFF files from the Cleveland Museum of Art. Review the contact sheet before uploading. Substitute `python3.13` for `python3.12` if needed.

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python frame.py pair --host TV_IP
.venv/bin/python frame.py prepare collections/cleveland-winter.json --out work/winter
# Open work/winter/preview-1.jpg before uploading.
.venv/bin/python frame.py push work/winter
```

The `prepare` command corrects source orientation and produces 3840 × 2160 sRGB JPEGs without upscaling. The export includes contact sheets, credits, and crop coordinates. [Research sources](SOURCES.md) · [Local collection format](RECIPE.md)

## Connection and compatibility

Accrochage is independent of Samsung. It uploads through the unofficial local Art Mode API using [samsungtvws](https://github.com/xchwarze/samsung-tv-ws-api), an LGPL-3.0 dependency you install separately. Compatibility depends on the model and firmware. Accrochage targets 4K displays in landscape orientation. The project code is under the [MIT license](LICENSE).

- If the TV is unreachable, check its address, Art Mode, and whether the guest network isolates devices. On macOS, grant local network access to the app running the agent or run the command from Terminal.
- Pairing state and upload receipts live under `~/.local/state/accrochage` or `$XDG_STATE_HOME/accrochage`. Retrying the same export skips images already confirmed on the TV. After an uncertain result, check **My Photos** before retrying.
- The credits include the license for each image. Those licenses come from the original sources.

Run the tests with `.venv/bin/python -m unittest discover -s tests -v`.
