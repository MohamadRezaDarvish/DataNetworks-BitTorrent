from pathlib import Path
import hashlib
import secrets
import socket
import threading
from urllib.request import urlopen
from urllib.parse import quote_from_bytes

from common.bencode import bencode, bendecode
from common.logger import log_event
from common.protocol import HANDSHAKE_LENGTH
from peer.messages import make_handshake, parse_handshake


PEER_HOST = "0.0.0.0"
TRACKER_TIMEOUT = 5
PEER_SOCKET_TIMEOUT = 5


# Read and decode a .torrent file
def load_torrent(torrent_path):
    data = Path(torrent_path).read_bytes()
    metainfo = bendecode(data)

    if not isinstance(metainfo, dict):
        raise TypeError("torrent must contain a dictionary")

    if b"announce" not in metainfo:
        raise ValueError("torrent has no announce field")

    if b"info" not in metainfo:
        raise ValueError("torrent has no info field")

    return metainfo


# Calculate the 20-byte torrent ID
def calculate_info_hash(info):
    return hashlib.sha1(
        bencode(info)
    ).digest()


# Create a readable 20-byte peer ID
def create_peer_id():
    random_part = secrets.token_hex(6).encode("ascii")

    peer_id = b"-DN0001-" + random_part

    return peer_id


# Get total torrent size
def get_total_size(info):
    if b"length" in info:
        return info[b"length"]

    if b"files" in info:
        total = 0

        for file_info in info[b"files"]:
            total += file_info[b"length"]

        return total

    raise ValueError("torrent has no file length information")


# Get tracker URL from torrent
def get_tracker_url(metainfo):
    tracker = metainfo[b"announce"]

    if isinstance(tracker, list):
        if not tracker:
            raise ValueError("announce list is empty")

        tracker = tracker[0]

    if isinstance(tracker, bytes):
        tracker = tracker.decode("utf-8")

    if not isinstance(tracker, str):
        raise ValueError("invalid tracker URL")

    return tracker


# Send an announce request to tracker
def announce_to_tracker(
    tracker_url,
    info_hash,
    peer_id,
    port,
    uploaded,
    downloaded,
    left,
    event=None
):
    if event not in [None, "started", "completed", "stopped"]:
        raise ValueError("invalid tracker event")

    url = (
        tracker_url
        + "?info_hash="
        + quote_from_bytes(info_hash, safe="")
        + "&peer_id="
        + quote_from_bytes(peer_id, safe="")
        + "&port="
        + str(port)
        + "&uploaded="
        + str(uploaded)
        + "&downloaded="
        + str(downloaded)
        + "&left="
        + str(left)
    )

    if event:
        url += "&event=" + event

    with urlopen(url, timeout=TRACKER_TIMEOUT) as response:
        data = response.read()

    tracker_response = bendecode(data)

    if b"failure reason" in tracker_response:
        reason = tracker_response[b"failure reason"]

        if isinstance(reason, bytes):
            reason = reason.decode("utf-8")

        raise RuntimeError(reason)

    return tracker_response


# Get peer list from tracker response
def get_peer_list(tracker_response):
    peers = tracker_response.get(b"peers", [])

    if not isinstance(peers, list):
        raise TypeError("invalid peer list")

    return peers


# Create the TCP socket that other peers connect to
def create_peer_server(port):
    server = socket.socket(
        socket.AF_INET,
        socket.SOCK_STREAM
    )

    server.setsockopt(
        socket.SOL_SOCKET,
        socket.SO_REUSEADDR,
        1
    )

    server.bind(
        (PEER_HOST, port)
    )

    server.listen()

    return server

# Wait for incoming peer connections
def accept_peer_connections(
    server,
    info_hash,
    peer_id,
    log_file,
    connections
):
    while True:
        try:
            sock, address = server.accept()
            sock.settimeout(PEER_SOCKET_TIMEOUT)

        except OSError:
            break

        thread = threading.Thread(
            target=handle_incoming_peer,
            args=(
                sock,
                address,
                info_hash,
                peer_id,
                log_file,
                connections
            ),
            daemon=True
        )

        thread.start()


# Connect to another peer
def connect_to_peer(ip, port, timeout=PEER_SOCKET_TIMEOUT):
    sock = socket.socket(
        socket.AF_INET,
        socket.SOCK_STREAM
    )

    sock.settimeout(timeout)

    sock.connect(
        (ip, port)
    )

    return sock

# Receive exactly the requested number of bytes
def recv_exact(sock, size):
    if size < 0:
        raise ValueError("size cannot be negative")

    data = b""

    while len(data) < size:
        chunk = sock.recv(size - len(data))

        if not chunk:
            raise ConnectionError(
                "peer closed the connection before all data was received"
            )

        data += chunk

    return data


# Send our handshake to another peer
def send_handshake(sock, info_hash, peer_id):
    handshake = make_handshake(info_hash, peer_id)
    sock.sendall(handshake)


# Receive and verify another peer's handshake
def receive_handshake(sock, expected_info_hash):
    data = recv_exact(sock, HANDSHAKE_LENGTH)

    handshake = parse_handshake(data)

    if handshake["info_hash"] != expected_info_hash:
        raise ValueError("peer handshake has the wrong info_hash")

    return handshake


# Handle a new incoming peer connection
def handle_incoming_peer(
    sock,
    address,
    info_hash,
    peer_id,
    log_file,
    connections
):
    try:
        remote_handshake = receive_handshake(
            sock,
            info_hash
        )

        send_handshake(
            sock,
            info_hash,
            peer_id
        )

        connections.append({
            "socket": sock,
            "address": address,
            "peer_id": remote_handshake["peer_id"]
        })

        log_event(
            log_file,
            "PEER_HANDSHAKE",
            f"Handshake completed with {address}"
        )

    except (ConnectionError, ValueError, socket.timeout, OSError) as error:
        log_event(
            log_file,
            "PEER_CONNECTION_FAILED",
            f"Connection from {address} failed: {error}"
        )

        sock.close()



# Connect to another peer and exchange handshakes
def connect_and_handshake(
    ip,
    port,
    info_hash,
    peer_id,
    log_file
):
    sock = None

    try:
        sock = connect_to_peer(
            ip,
            port
        )

        send_handshake(
            sock,
            info_hash,
            peer_id
        )

        remote_handshake = receive_handshake(
            sock,
            info_hash
        )

        log_event(
            log_file,
            "PEER_HANDSHAKE",
            f"Handshake completed with {(ip, port)}"
        )

        return {
            "socket": sock,
            "address": (ip, port),
            "peer_id": remote_handshake["peer_id"]
        }

    except (ConnectionError, ValueError, socket.timeout, OSError) as error:
        log_event(
            log_file,
            "PEER_CONNECTION_FAILED",
            f"Connection to {(ip, port)} failed: {error}"
        )

        if sock:
            sock.close()

        return None



# Prepare one peer and announce it to the tracker
def start_peer(torrent_path, port, left=None):
    metainfo = load_torrent(torrent_path)

    info = metainfo[b"info"]

    info_hash = calculate_info_hash(info)
    peer_id = create_peer_id()
    tracker_url = get_tracker_url(metainfo)

    total_size = get_total_size(info)

    if left is None:
        left = total_size

    log_file = f"logs/peer_{port}.log"

    # Listen before telling the tracker about this peer
    server = create_peer_server(port)

    log_event(
        log_file,
        "PEER_STARTED",
        f"Listening on port {port}"
    )

    try:
        tracker_response = announce_to_tracker(
            tracker_url=tracker_url,
            info_hash=info_hash,
            peer_id=peer_id,
            port=port,
            uploaded=0,
            downloaded=0,
            left=left,
            event="started"
        )
    except (OSError, RuntimeError, ValueError, TypeError):
        server.close()
        raise

    peers = get_peer_list(
        tracker_response
    )

    log_event(
        log_file,
        "TRACKER_RESPONSE",
        f"Received {len(peers)} peers"
    )

    connections = []

    accept_thread = threading.Thread(
        target=accept_peer_connections,
        args=(
            server,
            info_hash,
            peer_id,
            log_file,
            connections
        ),
        daemon=True
    )

    accept_thread.start()

    return {
        "metainfo": metainfo,
        "info": info,
        "info_hash": info_hash,
        "peer_id": peer_id,
        "tracker_url": tracker_url,
        "port": port,
        "uploaded": 0,
        "downloaded": 0,
        "left": left,
        "server": server,
        "peers": peers,
        "connections": connections,
        "accept_thread": accept_thread,
        "interval": tracker_response[b"interval"]
    }