"""Pair with one local TV and add prepared images through Samsung's Art Mode API."""

from contextlib import contextmanager, suppress
import fcntl
import hashlib
import ipaddress
import json
import logging
import os
from pathlib import Path
import signal
import tempfile
import time
import urllib.request

from samsungtvws import SamsungTVWS

from artwork import checked_output, json_text

LOCAL_NETWORKS = [ipaddress.ip_network(block) for block in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")]
STATE = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "accrochage"


def local_ip(host):
    address = ipaddress.IPv4Address(host)
    if not any(address in network for network in LOCAL_NETWORKS):
        raise ValueError("Use the private IPv4 address shown in the TV network settings")
    return str(address)


@contextmanager
def deadline(seconds, label):
    def expired(*_):
        raise TimeoutError(f"{label} timed out after {seconds} seconds")

    original = signal.signal(signal.SIGALRM, expired)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, original)


@contextmanager
def lock():
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    if STATE.stat().st_mode & 0o077:
        raise ValueError(f"Expected a private (0700) state directory at {STATE}")
    with (STATE / "lock").open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("Accrochage is already connected to the TV in another process") from exc
        yield


def read_state(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def write_state(path, state):
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False, encoding="utf-8") as stream:
        temporary = Path(stream.name)
        try:
            stream.write(json_text(state))
            stream.flush()
            os.fsync(stream.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def device(host):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *_):
            return None

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(f"http://{host}:8001/api/v2/", timeout=5) as response:
            info = json.loads(response.read(262144))
    except (OSError, ValueError) as exc:
        raise ValueError(
            "Cannot read the TV on the local network. Check its IP, Art Mode and local-network permissions."
        ) from exc
    details = info.get("device", {})
    identification = details.get("duid") or info.get("id") or details.get("wifiMac")
    model = details.get("modelName")
    if not isinstance(identification, str) or not identification or not isinstance(model, str) or not model:
        raise ValueError("Could not identify the television at the selected address")
    key = hashlib.sha256(identification.encode()).hexdigest()
    return {"host": host, "device_key": key, "model": model}


@contextmanager
def connect(record):
    host, key = record["host"], record["device_key"]
    token = STATE / f"{key}-token"
    if token.is_file():
        token.chmod(0o600)
    old_no_proxy = {name: os.environ.get(name) for name in ("NO_PROXY", "no_proxy")}
    for name, old in old_no_proxy.items():
        os.environ[name] = f"{old},{host}" if old else host
    old_mask = os.umask(0o077)
    logger = logging.getLogger("samsungtvws")
    old_level = logger.level
    logger.setLevel(logging.CRITICAL)
    tv = art = None
    try:
        tv = SamsungTVWS(host, port=8002, timeout=35, token_file=str(token), name="Accrochage")
        with deadline(40, "Remote channel pairing"):
            tv.open()
            tv.close()
        art = tv.art()
        with deadline(35, "Art channel"):
            art.open()
            api_version = art.get_api_version()
        print(f"Art API {api_version} · {record['model']}", flush=True)
        yield art
    finally:
        for connection in (art, tv):
            if connection is not None:
                with suppress(Exception), deadline(4, "Closing connection"):
                    connection.close()
        for name, old in old_no_proxy.items():
            if old is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = old
        os.umask(old_mask)
        logger.setLevel(old_level)
        if token.is_file():
            token.chmod(0o600)


def inventory(art):
    with deadline(25, "Art inventory"):
        available = art.available()
    if not isinstance(available, list) or any(
        not isinstance(item, dict) or not isinstance(item.get("content_id"), str) for item in available
    ):
        raise ValueError("Unexpected Art inventory response")
    return {item["content_id"] for item in available}


def pair(host):
    with lock():
        record = device(local_ip(host))
        print("Accept Accrochage on the TV if a pairing prompt appears.", flush=True)
        with connect(record) as art:
            inventory(art)
        write_state(STATE / "tv.json", record)
    print(f"Paired: {record['model']}", flush=True)


def add_images(art, files, state, save, resolve_id=None, retry_pending=False):
    present = inventory(art)
    receipts = state["receipts"]
    pending = state.get("pending")
    if pending:
        new_ids = present - set(pending["before"])
        if resolve_id in new_ids and resolve_id not in receipts.values():
            receipts[pending["sha256"]] = resolve_id
        elif not retry_pending:
            candidates = ", ".join(sorted(new_ids)) or "none"
            raise ValueError(
                f"Earlier upload needs verification: {pending['title']}. "
                f"New content IDs: {candidates}. Inspect My Photos before choosing --resolve-content-id "
                "or --retry-pending."
            )
        state["pending"] = None
        save(state)
    elif resolve_id or retry_pending:
        raise ValueError("No uncertain upload to resolve")
    sent = skipped = 0
    expected = set()
    for path, fingerprint, title in files:
        existing = receipts.get(fingerprint)
        if existing in present:
            print(f"Already on TV: {title} ({existing})", flush=True)
            skipped += 1
            expected.add(existing)
            continue
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != fingerprint:
            raise ValueError(f"{path.name} changed after verification")
        state["pending"] = {"title": title, "sha256": fingerprint, "before": sorted(present)}
        save(state)
        try:
            with deadline(90, "Image upload"):
                identifier = art.upload(data, matte="none", portrait_matte="none", file_type="jpg")
            if not isinstance(identifier, str) or not identifier or identifier in present:
                raise ValueError("The TV did not return a new content ID")
        except Exception as exc:
            raise ValueError(
                f"Upload result uncertain for {title} ({type(exc).__name__}). "
                "Check the TV inventory before retrying."
            ) from exc
        receipts[fingerprint] = identifier
        state["pending"] = None
        save(state)
        present.add(identifier)
        expected.add(identifier)
        sent += 1
        print(f"Added: {title} ({identifier})", flush=True)

    for delay in (0, 1, 2):
        if delay:
            time.sleep(delay)
        if expected <= inventory(art):
            print(f"Verified in Art Mode: {sent} added, {skipped} already present.", flush=True)
            return
    raise ValueError("The TV acknowledged uploads but its inventory has not confirmed every item")


def push(folder, resolve_id=None, retry_pending=False):
    files = checked_output(folder)
    with lock():
        record = read_state(STATE / "tv.json", None)
        if not record:
            raise ValueError("Run 'frame.py pair --host TV_IP' on the computer connected to your TV's network")
        actual = device(local_ip(record["host"]))
        if actual["device_key"] != record["device_key"]:
            raise ValueError("This address now belongs to a different device. Pair with the intended TV again")
        state_path = STATE / f"{record['device_key']}-uploads.json"
        state = read_state(state_path, {"receipts": {}, "pending": None})
        with connect(record) as art:
            add_images(art, files, state, lambda data: write_state(state_path, data), resolve_id, retry_pending)
