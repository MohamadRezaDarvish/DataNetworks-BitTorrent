from pathlib import Path
import sys
import time
import threading

from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlsplit, unquote_to_bytes

from common.bencode import bencode
from common.logger import log_event


TRACKER_IP = "0.0.0.0"
TRACKER_PORT = 6969

INTERVAL = 30
PEER_TIMEOUT = 90

LOG_FILE = "logs/tracker.log"

swarms = {}
lock = threading.Lock()


# Read tracker query parameters
def parse_query(query):
    params = {}

    for part in query.split("&"):
        if "=" not in part:
            continue

        key, value = part.split("=", 1)

        params[key] = unquote_to_bytes(value)

    return params


# Add a new peer or update an existing peer
def add_or_update_peer(
    info_hash,
    peer_id,
    ip,
    port,
    uploaded,
    downloaded,
    left
):
    with lock:
        if info_hash not in swarms:
            swarms[info_hash] = {}

        swarms[info_hash][peer_id] = {
            "ip": ip,
            "port": port,
            "uploaded": uploaded,
            "downloaded": downloaded,
            "left": left,
            "last_seen": time.monotonic()
        }


# Remove one peer
def remove_peer(info_hash, peer_id):
    with lock:
        if info_hash not in swarms:
            return

        swarms[info_hash].pop(peer_id, None)

        if not swarms[info_hash]:
            del swarms[info_hash]


# Check whether one peer is already registered
def peer_is_registered(info_hash, peer_id):
    with lock:
        return peer_id in swarms.get(info_hash, {})


# Remove peers that stopped announcing
def remove_stale_peers():
    now = time.monotonic()

    with lock:
        empty_swarms = []

        for info_hash, peers in swarms.items():
            stale_peers = []

            for peer_id, peer in peers.items():
                if now - peer["last_seen"] > PEER_TIMEOUT:
                    stale_peers.append(peer_id)

            for peer_id in stale_peers:
                del peers[peer_id]

                log_event(
                    LOG_FILE,
                    "PEER_TIMEOUT",
                    "Removed stale peer"
                )

            if not peers:
                empty_swarms.append(info_hash)

        for info_hash in empty_swarms:
            del swarms[info_hash]


# Return the peer list for one torrent
def get_peers(info_hash, exclude_peer_id=None):
    result = []

    with lock:
        peers = swarms.get(info_hash, {})

        for peer_id, peer in peers.items():
            if peer_id == exclude_peer_id:
                continue

            result.append({
                b"peer_id": peer_id,
                b"ip": peer["ip"].encode("ascii"),
                b"port": peer["port"]
            })

    return result


# Count seeders and leechers
def get_counts(info_hash):
    complete = 0
    incomplete = 0

    with lock:
        peers = swarms.get(info_hash, {})

        for peer in peers.values():
            if peer["left"] == 0:
                complete += 1
            else:
                incomplete += 1

    return complete, incomplete


# Send a bencoded tracker response
def send_response(handler, response):
    body = bencode(response)

    handler.send_response(200)
    handler.send_header("Content-Type", "text/plain")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()

    handler.wfile.write(body)


# Send a tracker failure response
def send_failure(handler, message):
    log_event(
        LOG_FILE,
        "TRACKER_ERROR",
        message
    )

    send_response(
        handler,
        {
            b"failure reason": message.encode("utf-8")
        }
    )


class TrackerHandler(BaseHTTPRequestHandler):

    # Handle peer GET request
    def do_GET(self):
        remove_stale_peers()

        url = urlsplit(self.path)

        if url.path != "/announce":
            send_failure(self, "Invalid tracker path")
            return

        params = parse_query(url.query)

        required = [
            "info_hash",
            "peer_id",
            "port",
            "uploaded",
            "downloaded",
            "left"
        ]

        for name in required:
            if name not in params:
                send_failure(
                    self,
                    f"Missing parameter: {name}"
                )
                return

        try:
            info_hash = params["info_hash"]
            peer_id = params["peer_id"]

            port = int(params["port"])
            uploaded = int(params["uploaded"])
            downloaded = int(params["downloaded"])
            left = int(params["left"])

            event = params.get("event")
        except (ValueError, TypeError):
            send_failure(
                self,
                "Invalid parameter value"
            )
            return

        if len(info_hash) != 20:
            send_failure(
                self,
                "info_hash must be 20 bytes"
            )
            return

        if len(peer_id) != 20:
            send_failure(
                self,
                "peer_id must be 20 bytes"
            )
            return

        if not 1 <= port <= 65535:
            send_failure(
                self,
                "Invalid peer port"
            )
            return

        if uploaded < 0 or downloaded < 0 or left < 0:
            send_failure(
                self,
                "Byte counters cannot be negative"
            )
            return

        if event is not None:
            try:
                event = event.decode("ascii")
            except UnicodeDecodeError:
                send_failure(
                    self,
                    "Invalid event"
                )
                return

            if event == "":
                event = None

            elif event not in ["started", "completed", "stopped"]:
                send_failure(
                    self,
                    "Unknown event"
                )
                return

        if event != "started" and not peer_is_registered(info_hash, peer_id):
            send_failure(
                self,
                "First announce must include event=started"
            )
            return

        ip = self.client_address[0]

        if event == "stopped":
            remove_peer(
                info_hash,
                peer_id
            )

            log_event(
                LOG_FILE,
                "PEER_STOPPED",
                f"{ip}:{port}"
            )

        else:
            add_or_update_peer(
                info_hash,
                peer_id,
                ip,
                port,
                uploaded,
                downloaded,
                left
            )

            if event == "started":
                log_event(
                    LOG_FILE,
                    "PEER_STARTED",
                    f"{ip}:{port}"
                )

            elif event == "completed":
                log_event(
                    LOG_FILE,
                    "PEER_COMPLETED",
                    f"{ip}:{port}"
                )

            else:
                log_event(
                    LOG_FILE,
                    "PEER_UPDATE",
                    f"{ip}:{port}"
                )

        complete, incomplete = get_counts(info_hash)

        peers = get_peers(
            info_hash,
            exclude_peer_id=peer_id
        )

        response = {
            b"interval": INTERVAL,
            b"complete": complete,
            b"incomplete": incomplete,
            b"peers": peers
        }

        log_event(
            LOG_FILE,
            "TRACKER_RESPONSE",
            f"Returned {len(peers)} peers to {ip}:{port}"
        )

        send_response(
            self,
            response
        )

    # Stop normal HTTP request messages in terminal
    def log_message(self, format, *args):
        return


# Start tracker server
def run_tracker():
    server = ThreadingHTTPServer(
        (TRACKER_IP, TRACKER_PORT),
        TrackerHandler
    )

    log_event(
        LOG_FILE,
        "TRACKER_STARTED",
        f"Tracker listening on port {TRACKER_PORT}"
    )

    print(
        f"Tracker running at "
        f"http://127.0.0.1:{TRACKER_PORT}/announce"
    )

    server.serve_forever()


# Start tracker when this file is executed
if Path(sys.argv[0]).name == "tracker.py":
    run_tracker()
