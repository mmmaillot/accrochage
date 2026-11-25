"""Search published museum and Wikimedia APIs and fetch verified source assets."""

import html
import json
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path

CMA = "openaccess-api.clevelandart.org"
COMMONS = "commons.wikimedia.org"
SEARCH_HOSTS = {CMA, COMMONS}
IMAGE_HOSTS = {
    "openaccess-cdn.clevelandart.org", "upload.wikimedia.org", "images.metmuseum.org",
    "collectionapi.metmuseum.org", "www.artic.edu", "lakeimagesweb.artic.edu",
}
USER_AGENT = os.environ.get("ACCROCHAGE_USER_AGENT", "Accrochage/0.2 (personal art collection tool)")
IMAGE_LIMIT = 300 * 1024 * 1024


def checked_url(url, hosts=None):
    parts = urllib.parse.urlsplit(url)
    if (parts.scheme != "https" or not parts.hostname or parts.username or parts.password
            or parts.port not in (None, 443) or (hosts is not None and parts.hostname not in hosts)):
        raise ValueError(f"Unexpected HTTPS URL or host: {url}")
    return url


class CheckedRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, hosts):
        self.hosts = hosts

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        checked_url(newurl, self.hosts)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_url(url, hosts, accept):
    request = urllib.request.Request(checked_url(url, hosts), headers={
        "User-Agent": USER_AGENT, "Accept": accept,
    })
    opener = urllib.request.build_opener(CheckedRedirect(hosts))
    return opener.open(request, timeout=30)


def download(url, path: Path):
    with open_url(url, IMAGE_HOSTS, "image/*") as response, path.open("xb") as stream:
        total = 0
        while chunk := response.read(1024 * 1024):
            total += len(chunk)
            if total > IMAGE_LIMIT:
                raise ValueError("Original exceeds the 300 MiB download limit")
            stream.write(chunk)


def api(host, endpoint, params):
    query = urllib.parse.urlencode(params)
    with open_url(f"https://{host}/{endpoint}?{query}", SEARCH_HOSTS, "application/json") as response:
        payload = response.read(5_000_001)
    if len(payload) > 5_000_000:
        raise ValueError("Search response exceeds the size limit")
    data = json.loads(payload)
    if "error" in data:
        raise ValueError(f"Search API error: {data['error'].get('info', 'unknown error')}")
    return data


def cleveland(query, limit):
    params = {"q": query, "cc0": "", "has_image": 1, "limit": limit}
    for artwork in api(CMA, "api/artworks", params)["data"]:
        images = artwork.get("images") or {}
        image = images.get("full") or images.get("print") or {}
        if artwork.get("share_license_status") != "CC0" or not image.get("url"):
            continue
        yield {
            "title": artwork["title"],
            "author": "; ".join(c["description"] for c in artwork.get("creators", []) if c.get("description")),
            "date": artwork.get("creation_date"),
            "source": artwork["url"],
            "image_url": image["url"],
            "preview_url": (images.get("web") or {}).get("url"),
            "license": "CC0 1.0",
            "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
            "credit": f"The Cleveland Museum of Art, {artwork.get('creditline', '')} {artwork['accession_number']}",
            "original_size": [image.get("width"), image.get("height")],
        }


def commons(query, limit):
    params = {
        "action": "query", "format": "json", "formatversion": 2, "generator": "search",
        "gsrsearch": f"{query} filetype:bitmap", "gsrnamespace": 6, "gsrlimit": limit,
        "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata", "iiurlwidth": 680,
        "iiextmetadatafilter": "Artist|Credit|DateTimeOriginal|LicenseShortName|LicenseUrl|Restrictions",
    }
    for page in api(COMMONS, "w/api.php", params).get("query", {}).get("pages", []):
        if not page.get("imageinfo"):
            continue
        info = page["imageinfo"][0]
        metadata = info.get("extmetadata", {})

        def field(key):
            return html.unescape(re.sub(r"<[^>]+>", " ", metadata.get(key, {}).get("value", ""))).strip()

        yield {
            "title": page["title"].removeprefix("File:"), "author": field("Artist"),
            "date": field("DateTimeOriginal"), "source": info["descriptionurl"], "image_url": info["url"],
            "preview_url": info.get("thumburl"), "license": field("LicenseShortName"),
            "license_url": field("LicenseUrl"), "credit": field("Credit"),
            "restrictions_hint": field("Restrictions"), "original_size": [info["width"], info["height"]],
        }
