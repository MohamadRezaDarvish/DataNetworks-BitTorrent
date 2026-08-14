import hashlib
import secrets
import socket
import struct
import threading
from pathlib import Path
from urllib.parse import quote_from_bytes
from urllib.request import urlopen

from common.bencode import bencode, bendecode
from common.logger import log_event
from common.protocol import HANDSHAKE_LENGTH, LENGTH_PREFIX_SIZE
from peer.messages import (
    make_bitfield,
    make_handshake,
    make_have,
    make_interested,
    make_keep_alive,
    make_not_interested,
    make_piece,
    make_request,
    make_unchoke,
    parse_handshake,
    parse_message,
)
from peer.piece_manager import (
    create_piece_manager,
    get_bitfield,
    get_bytes_left,
    get_missing_pieces,
    get_piece_count,
    get_piece_size,
    have_piece,
    is_complete,
    read_piece_block,
    save_piece,
)

PEER_HOST = "0.0.0.0"
TRACKER_TIMEOUT = 5
PEER_SOCKET_TIMEOUT = 5
PEER_PING_INTERVAL = 15
MAX_BLOCK_SIZE = 16384

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
    return hashlib.sha1(bencode(info)).digest()

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
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((PEER_HOST, port))
    server.listen()

    return server

# Wait for incoming peer connections
def accept_peer_connections(
    server,
    info_hash,
    peer_id,
    log_file,
    connections,
    piece_manager,
    peer=None
):
    while True:
        try:
            sock, address = server.accept()
            sock.settimeout(PEER_SOCKET_TIMEOUT)

        except OSError:
            break

        thread = threading.Thread(
            target=handle_incoming_peer,
            args=(sock, address, info_hash, peer_id, log_file, connections, piece_manager, peer),
            daemon=True
        )
        thread.start()

# Connect to another peer
def connect_to_peer(ip, port, timeout=PEER_SOCKET_TIMEOUT):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    sock.connect((ip, port))

    return sock

# Receive exactly the requested number of bytes
def recv_exact(sock, size):
    if size < 0:
        raise ValueError("size cannot be negative")

    data = b""

    while len(data) < size:
        chunk = sock.recv(size - len(data))

        if not chunk:
            raise ConnectionError("peer closed the connection before all data was received")

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

# Send one normal peer message
def send_peer_message(sock, message):
    if not isinstance(message, bytes):
        raise TypeError("message must be bytes")

    sock.sendall(message)

# Receive one complete normal peer message
def receive_peer_message(sock):
    length_data = recv_exact(sock, LENGTH_PREFIX_SIZE)
    length = struct.unpack(">I", length_data)[0]

    if length == 0:
        return parse_message(length_data)

    message_data = recv_exact(sock, length)

    return parse_message(length_data + message_data)

# Create the state for one connected peer
def create_connection_state(sock, address, remote_peer_id):
    return {
        "socket": sock,
        "address": address,
        "peer_id": remote_peer_id,
        "am_choking": True,
        "am_choked": True,
        "am_interested": False,
        "remote_interested": False,
        "remote_bitfield": b"",
        "remote_pieces": set(),
        "received_bitfield": False,
        "sent_bitfield": False,
        "connected": True,
        "message_thread": None,
        "send_lock": threading.Lock(),
        "download_piece": None,
        "download_data": bytearray(),
        "request_begin": None,
        "request_length": 0
    }

# Send one message without mixing concurrent writes
def send_connection_message(connection, message):
    with connection["send_lock"]:
        send_peer_message(connection["socket"], message)

# Decode one remote piece bitfield
def decode_bitfield(piece_manager, bitfield):
    if not isinstance(bitfield, bytes):
        raise TypeError("bitfield must be bytes")

    piece_count = get_piece_count(piece_manager)
    expected_size = (piece_count + 7) // 8

    if len(bitfield) != expected_size:
        raise ValueError("bitfield has the wrong length")

    used_bits = piece_count % 8

    if used_bits and bitfield:
        spare_bits = 8 - used_bits
        spare_mask = (1 << spare_bits) - 1

        if bitfield[-1] & spare_mask:
            raise ValueError("bitfield has nonzero spare bits")

    pieces = set()

    for piece_index in range(piece_count):
        byte_index = piece_index // 8
        bit_index = 7 - piece_index % 8

        if bitfield[byte_index] & (1 << bit_index):
            pieces.add(piece_index)

    return pieces

# Send this peer's current piece bitfield
def send_local_bitfield(connection, piece_manager, log_file):
    bitfield = get_bitfield(piece_manager)
    send_connection_message(connection, make_bitfield(bitfield))
    connection["sent_bitfield"] = True
    log_event(
        log_file,
        "BITFIELD_SENT",
        f"Sent bitfield {bitfield.hex()} to {connection['address']}"
    )

# Update whether this peer is interested in a remote peer
def update_interest(connection, piece_manager, log_file):
    missing_pieces = set(get_missing_pieces(piece_manager))
    interested = bool(missing_pieces & connection["remote_pieces"])

    if interested == connection["am_interested"]:
        return

    if interested:
        message = make_interested()
        event_type = "INTERESTED_SENT"
        description = (f"Interested in pieces from {connection['address']}")
    else:
        message = make_not_interested()
        event_type = "NOT_INTERESTED_SENT"
        description = (f"No longer interested in {connection['address']}")

    send_connection_message(connection, message)
    connection["am_interested"] = interested
    log_event(log_file, event_type, description)

# Release one piece reserved by a connection
def release_requested_piece(connection, peer):
    piece_index = connection["download_piece"]

    if peer is not None and piece_index is not None:
        with peer["download_lock"]:
            peer["requested_pieces"].discard(piece_index)

    connection["download_piece"] = None
    connection["download_data"] = bytearray()
    connection["request_begin"] = None
    connection["request_length"] = 0

# Reserve one missing piece from a connected peer
def reserve_requested_piece(connection, peer):
    if connection["download_piece"] is not None:
        return connection["download_piece"]

    with peer["download_lock"]:
        missing_pieces = set(get_missing_pieces(peer["piece_manager"]))
        available_pieces = sorted(missing_pieces & connection["remote_pieces"])

        for piece_index in available_pieces:
            if piece_index in peer["requested_pieces"]:
                continue

            peer["requested_pieces"].add(piece_index)
            connection["download_piece"] = piece_index
            connection["download_data"] = bytearray()

            return piece_index

    return None

# Request the next block from one peer
def request_next_block(connection, peer):
    if (
        peer is None
        or not peer["running"]
        or not connection["connected"]
        or connection["am_choked"]
        or not connection["am_interested"]
        or connection["request_begin"] is not None
    ):
        return False

    piece_index = reserve_requested_piece(connection, peer)

    if piece_index is None:
        return False

    begin = len(connection["download_data"])
    piece_size = get_piece_size(peer["piece_manager"], piece_index)
    length = min(MAX_BLOCK_SIZE, piece_size - begin)

    if length <= 0:
        release_requested_piece(connection, peer)
        return False

    connection["request_begin"] = begin
    connection["request_length"] = length

    try:
        send_connection_message(connection, make_request(piece_index, begin, length))
    except OSError:
        connection["request_begin"] = None
        connection["request_length"] = 0
        raise

    log_event(
        peer["log_file"],
        "REQUEST_SENT",
        f"Requested piece {piece_index} bytes {begin}:{begin + length} "
        f"from {connection['address']}"
    )

    return True

# Send one tracker announce for a running peer
def announce_peer(peer, event=None):
    with peer["tracker_lock"]:
        response = announce_to_tracker(
            tracker_url=peer["tracker_url"],
            info_hash=peer["info_hash"],
            peer_id=peer["peer_id"],
            port=peer["port"],
            uploaded=peer["uploaded"],
            downloaded=peer["downloaded"],
            left=peer["left"],
            event=event
        )

    peer["peers"] = get_peer_list(response)
    peer["interval"] = response[b"interval"]

    return response

# Announce one completed download
def announce_completed(peer):
    if not is_complete(peer["piece_manager"]):
        return False

    with peer["state_lock"]:
        if peer["completed_announced"]:
            return False

        peer["completed_announced"] = True
        peer["left"] = 0

    try:
        announce_peer(peer, event="completed")
    except (OSError, RuntimeError, ValueError, TypeError) as error:
        with peer["state_lock"]:
            peer["completed_announced"] = False

        log_event(peer["log_file"], "TRACKER_ERROR", f"Completed announce failed: {error}")

        return False

    log_event(peer["log_file"], "DOWNLOAD_COMPLETED", "All pieces were downloaded and verified")

    return True

# Send one requested block to a peer
def handle_request_message(connection, piece_manager, log_file, message, peer=None):
    piece_index = message["piece_index"]
    begin = message["begin"]
    length = message["length"]

    if connection["am_choking"] or not connection["remote_interested"]:
        log_event(
            log_file,
            "REQUEST_REJECTED",
            f"Rejected request from {connection['address']} while choked"
        )
        return

    if length > MAX_BLOCK_SIZE:
        raise ValueError("requested block is too large")

    try:
        block = read_piece_block(piece_manager, piece_index, begin, length)
    except (ValueError, IndexError) as error:
        log_event(
            log_file,
            "REQUEST_REJECTED",
            f"Rejected request from {connection['address']}: {error}"
        )
        return

    send_connection_message(connection, make_piece(piece_index, begin, block))

    if peer is not None:
        with peer["state_lock"]:
            peer["uploaded"] += len(block)

    log_event(
        log_file,
        "PIECE_SENT",
        f"Sent piece {piece_index} bytes {begin}:{begin + len(block)} "
        f"to {connection['address']}"
    )

# Verify and save one received piece block
def handle_piece_message(connection, piece_manager, log_file, message, peer):
    if peer is None:
        raise ValueError("cannot save PIECE without peer state")

    piece_index = message["piece_index"]
    begin = message["begin"]
    block = message["block"]

    if connection["request_begin"] is None:
        raise ValueError("received an unrequested PIECE")

    if piece_index != connection["download_piece"]:
        raise ValueError("PIECE index does not match request")

    if begin != connection["request_begin"]:
        raise ValueError("PIECE offset does not match request")

    if len(block) != connection["request_length"]:
        raise ValueError("PIECE length does not match request")

    connection["download_data"].extend(block)
    connection["request_begin"] = None
    connection["request_length"] = 0

    with peer["state_lock"]:
        peer["downloaded"] += len(block)

    log_event(
        log_file,
        "PIECE_RECEIVED",
        f"Received piece {piece_index} bytes {begin}:{begin + len(block)} "
        f"from {connection['address']}"
    )
    piece_size = get_piece_size(piece_manager, piece_index)

    if len(connection["download_data"]) < piece_size:
        request_next_block(connection, peer)
        return

    if len(connection["download_data"]) > piece_size:
        raise ValueError("received piece exceeds expected size")

    piece_data = bytes(connection["download_data"])
    saved = save_piece(piece_manager, piece_index, piece_data)
    release_requested_piece(connection, peer)

    if not saved:
        log_event(log_file, "PIECE_REJECTED", f"Piece {piece_index} failed SHA-1 verification")
        request_next_block(connection, peer)
        return

    left = get_bytes_left(piece_manager)

    with peer["state_lock"]:
        peer["left"] = left

    log_event(log_file, "PIECE_VERIFIED", f"Piece {piece_index} passed SHA-1 verification")
    send_have_to_peers(peer["connections"], piece_manager, piece_index, log_file)

    if left == 0:
        announce_completed(peer)
        return

    request_next_block(connection, peer)

# Handle one parsed message from a connected peer
def handle_received_message(connection, piece_manager, log_file, message, peer=None):
    message_type = message["type"]
    address = connection["address"]

    if message_type == "keep_alive":
        log_event(log_file, "PEER_PING_RECEIVED", f"Received keep-alive from {address}")
        return

    if message_type == "bitfield":
        if piece_manager is None:
            raise ValueError("cannot read bitfield without piece manager")

        if connection["received_bitfield"]:
            raise ValueError("peer sent more than one bitfield")

        remote_pieces = decode_bitfield(piece_manager, message["bitfield"])
        connection["remote_bitfield"] = message["bitfield"]
        connection["remote_pieces"] = remote_pieces
        connection["received_bitfield"] = True
        log_event(
            log_file,
            "BITFIELD_RECEIVED",
            f"Peer {address} has pieces {sorted(remote_pieces)}"
        )

        if not connection["sent_bitfield"]:
            send_local_bitfield(connection, piece_manager, log_file)

        update_interest(connection, piece_manager, log_file)
        request_next_block(connection, peer)

        return

    if message_type == "have":
        if piece_manager is None:
            raise ValueError("cannot read HAVE without piece manager")

        piece_index = message["piece_index"]
        piece_count = get_piece_count(piece_manager)

        if piece_index >= piece_count:
            raise ValueError("HAVE piece index is out of range")

        connection["remote_pieces"].add(piece_index)
        bitfield_size = (piece_count + 7) // 8

        if connection["remote_bitfield"]:
            remote_bitfield = bytearray(connection["remote_bitfield"])
        else:
            remote_bitfield = bytearray(bitfield_size)

        byte_index = piece_index // 8
        bit_index = 7 - piece_index % 8
        remote_bitfield[byte_index] |= 1 << bit_index
        connection["remote_bitfield"] = bytes(remote_bitfield)
        log_event(log_file, "HAVE_RECEIVED", f"Peer {address} now has piece {piece_index}")
        update_interest(connection, piece_manager, log_file)
        request_next_block(connection, peer)

        return

    if message_type == "interested":
        connection["remote_interested"] = True
        log_event(log_file, "INTERESTED_RECEIVED", f"Peer {address} is interested")

        if connection["am_choking"]:
            connection["am_choking"] = False

            try:
                send_connection_message(connection, make_unchoke())
            except OSError:
                connection["am_choking"] = True
                raise

            log_event(log_file, "UNCHOKE_SENT", f"Unchoked peer {address}")

        return

    if message_type == "not_interested":
        connection["remote_interested"] = False
        log_event(log_file, "NOT_INTERESTED_RECEIVED", f"Peer {address} is not interested")

        return

    if message_type == "choke":
        connection["am_choked"] = True
        log_event(log_file, "CHOKE_RECEIVED", f"Peer {address} choked us")

        return

    if message_type == "unchoke":
        connection["am_choked"] = False
        log_event(log_file, "UNCHOKE_RECEIVED", f"Peer {address} unchoked us")
        request_next_block(connection, peer)

        return

    if message_type == "request":
        if piece_manager is None:
            raise ValueError("cannot serve REQUEST without piece manager")

        handle_request_message(connection, piece_manager, log_file, message, peer)

        return

    if message_type == "piece":
        if piece_manager is None:
            raise ValueError("cannot save PIECE without piece manager")

        handle_piece_message(connection, piece_manager, log_file, message, peer)

        return

    log_event(log_file, "PEER_MESSAGE", f"Received {message_type} from {address}")

# Handle messages received from one connected peer
def handle_peer_messages(sock, address, connection, log_file, piece_manager=None, peer=None):
    while True:
        try:
            message = receive_peer_message(sock)
            handle_received_message(connection, piece_manager, log_file, message, peer)

        except socket.timeout:
            continue

        except (ConnectionError, ValueError, TypeError, IndexError, OSError) as error:
            connection["connected"] = False
            release_requested_piece(connection, peer)

            if peer is None or peer["running"]:
                log_event(
                    log_file,
                    "PEER_DISCONNECTED",
                    f"Connection with {address} ended: {error}"
                )

            try:
                sock.close()
            except OSError:
                pass

            return

# Handle a new incoming peer connection
def handle_incoming_peer(
    sock,
    address,
    info_hash,
    peer_id,
    log_file,
    connections,
    piece_manager,
    peer=None
):
    try:
        remote_handshake = receive_handshake(sock, info_hash)
        send_handshake(sock, info_hash, peer_id)
        connection = create_connection_state(sock, address, remote_handshake["peer_id"])

        if peer is None:
            connections.append(connection)
        else:
            with peer["connections_lock"]:
                connections.append(connection)

        log_event(log_file, "PEER_HANDSHAKE", f"Handshake completed with {address}")
        handle_peer_messages(sock, address, connection, log_file, piece_manager, peer)

    except (ConnectionError, ValueError, TypeError, socket.timeout, OSError) as error:
        log_event(log_file, "PEER_CONNECTION_FAILED", f"Connection from {address} failed: {error}")

        try:
            sock.close()
        except OSError:
            pass

# Connect to another peer and exchange handshakes
def connect_and_handshake(ip, port, info_hash, peer_id, log_file, piece_manager=None):
    sock = None

    try:
        sock = connect_to_peer(ip, port)
        send_handshake(sock, info_hash, peer_id)
        remote_handshake = receive_handshake(sock, info_hash)
        connection = create_connection_state(sock, (ip, port), remote_handshake["peer_id"])
        log_event(log_file, "PEER_HANDSHAKE", f"Handshake completed with {(ip, port)}")

        if piece_manager is not None:
            send_local_bitfield(connection, piece_manager, log_file)

        return connection

    except (ConnectionError, ValueError, TypeError, socket.timeout, OSError) as error:
        log_event(log_file, "PEER_CONNECTION_FAILED", f"Connection to {(ip, port)} failed: {error}")

        if sock:
            sock.close()

        return None

# Start the message reader for one outgoing connection
def start_peer_message_thread(connection, piece_manager, log_file, peer=None):
    thread = threading.Thread(
        target=handle_peer_messages,
        args=(
            connection["socket"],
            connection["address"],
            connection,
            log_file,
            piece_manager,
            peer
        ),
        daemon=True
    )
    connection["message_thread"] = thread
    thread.start()

    return thread

# Connect one running peer to another peer
def connect_peer(peer, ip, port):
    if not peer["running"]:
        return None

    connection = connect_and_handshake(
        ip=ip,
        port=port,
        info_hash=peer["info_hash"],
        peer_id=peer["peer_id"],
        log_file=peer["log_file"],
        piece_manager=peer["piece_manager"]
    )

    if connection is None:
        return None

    with peer["connections_lock"]:
        for existing in peer["connections"]:
            if (existing["connected"] and existing["peer_id"] == connection["peer_id"]):
                connection["socket"].close()
                return existing

        peer["connections"].append(connection)

    start_peer_message_thread(connection, peer["piece_manager"], peer["log_file"], peer)

    return connection

# Tell every connected peer about one completed piece
def send_have_to_peers(connections, piece_manager, piece_index, log_file):
    if not have_piece(piece_manager, piece_index):
        raise ValueError("cannot announce a missing piece")

    message = make_have(piece_index)
    sent_count = 0

    for connection in list(connections):
        if not connection["connected"]:
            continue

        try:
            send_connection_message(connection, message)
            sent_count += 1
            log_event(log_file, "HAVE_SENT", f"Sent piece {piece_index} to {connection['address']}")
            update_interest(connection, piece_manager, log_file)

        except OSError as error:
            connection["connected"] = False
            log_event(
                log_file,
                "PEER_DISCONNECTED",
                f"Could not send HAVE to {connection['address']}: {error}"
            )

    return sent_count

# Prepare one peer and announce it to the tracker
def start_peer(torrent_path, port, left=None, data_root=None):
    metainfo = load_torrent(torrent_path)
    info = metainfo[b"info"]
    info_hash = calculate_info_hash(info)
    peer_id = create_peer_id()
    tracker_url = get_tracker_url(metainfo)

    if data_root is None:
        if left == 0:
            data_root = "shared_files"
        else:
            data_root = "downloads"

    piece_manager = create_piece_manager(info, data_root)
    left = get_bytes_left(piece_manager)
    log_file = f"logs/peer_{port}.log"

    # Listen before telling the tracker about this peer
    server = create_peer_server(port)
    log_event(log_file, "PEER_STARTED", f"Listening on port {port}")

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

    peers = get_peer_list(tracker_response)
    log_event(log_file, "TRACKER_RESPONSE", f"Received {len(peers)} peers")
    connections = []
    peer = {
        "metainfo": metainfo,
        "info": info,
        "info_hash": info_hash,
        "peer_id": peer_id,
        "tracker_url": tracker_url,
        "port": port,
        "uploaded": 0,
        "downloaded": 0,
        "left": left,
        "data_root": Path(data_root),
        "piece_manager": piece_manager,
        "log_file": log_file,
        "server": server,
        "peers": peers,
        "connections": connections,
        "accept_thread": None,
        "tracker_thread": None,
        "ping_thread": None,
        "interval": tracker_response[b"interval"],
        "running": True,
        "tasks_started": False,
        "completed_announced": left == 0,
        "requested_pieces": set(),
        "stop_event": threading.Event(),
        "state_lock": threading.Lock(),
        "download_lock": threading.Lock(),
        "tracker_lock": threading.Lock(),
        "connections_lock": threading.Lock()
    }
    accept_thread = threading.Thread(
        target=accept_peer_connections,
        args=(server, info_hash, peer_id, log_file, connections, piece_manager, peer),
        daemon=True
    )
    peer["accept_thread"] = accept_thread
    accept_thread.start()

    return peer

# Read one peer returned by the tracker
def read_tracker_peer(peer_info):
    if not isinstance(peer_info, dict):
        raise TypeError("tracker peer must be a dictionary")

    required = [b"peer_id", b"ip", b"port"]

    for field in required:
        if field not in peer_info:
            raise ValueError("tracker peer is missing a field")

    remote_peer_id = peer_info[b"peer_id"]
    ip = peer_info[b"ip"]
    port = peer_info[b"port"]

    if not isinstance(remote_peer_id, bytes) or len(remote_peer_id) != 20:
        raise ValueError("tracker peer_id must be 20 bytes")

    if isinstance(ip, bytes):
        ip = ip.decode("ascii")

    if not isinstance(ip, str):
        raise TypeError("tracker peer IP must be bytes or a string")

    if isinstance(port, bool) or not isinstance(port, int):
        raise TypeError("tracker peer port must be an integer")

    if not 1 <= port <= 65535:
        raise ValueError("tracker peer port is invalid")

    return remote_peer_id, ip, port

# Connect to peers returned by the tracker
def connect_discovered_peers(peer, tracker_peers):
    connected_count = 0

    for peer_info in tracker_peers:
        try:
            remote_peer_id, ip, port = read_tracker_peer(peer_info)
        except (UnicodeDecodeError, ValueError, TypeError) as error:
            log_event(peer["log_file"], "TRACKER_ERROR", f"Ignored invalid tracker peer: {error}")
            continue

        if remote_peer_id == peer["peer_id"] or port == peer["port"]:
            continue

        with peer["connections_lock"]:
            already_connected = any(
                connection["connected"]
                and connection["peer_id"] == remote_peer_id
                for connection in peer["connections"]
            )

        if already_connected:
            continue

        connection = connect_peer(peer, ip, port)

        if connection is not None:
            connected_count += 1

    return connected_count

# Refresh the tracker peer list periodically
def tracker_update_loop(peer):
    while peer["running"]:
        interval = peer["interval"]

        if isinstance(interval, bool) or not isinstance(interval, int):
            interval = 30

        if interval <= 0:
            interval = 30

        if peer["stop_event"].wait(interval):
            break

        try:
            response = announce_peer(peer)
            log_event(peer["log_file"], "TRACKER_RESPONSE", f"Received {len(peer['peers'])} peers")
            connect_discovered_peers(peer, get_peer_list(response))

        except (OSError, RuntimeError, ValueError, TypeError) as error:
            log_event(peer["log_file"], "TRACKER_ERROR", f"Periodic announce failed: {error}")

# Ping connected peers periodically
def peer_ping_loop(peer):
    while peer["running"]:
        if peer["stop_event"].wait(PEER_PING_INTERVAL):
            break

        with peer["connections_lock"]:
            connections = list(peer["connections"])

        for connection in connections:
            if not connection["connected"]:
                continue

            try:
                send_connection_message(connection, make_keep_alive())
                log_event(
                    peer["log_file"],
                    "PEER_PING_SENT",
                    f"Sent keep-alive to {connection['address']}"
                )

            except OSError as error:
                connection["connected"] = False
                release_requested_piece(connection, peer)
                log_event(
                    peer["log_file"],
                    "PEER_DISCONNECTED",
                    f"Ping to {connection['address']} failed: {error}"
                )

# Start automatic tracker and peer tasks
def start_peer_tasks(peer):
    with peer["state_lock"]:
        if peer["tasks_started"]:
            return

        peer["tasks_started"] = True

    tracker_thread = threading.Thread(target=tracker_update_loop, args=(peer,), daemon=True)
    ping_thread = threading.Thread(target=peer_ping_loop, args=(peer,), daemon=True)
    peer["tracker_thread"] = tracker_thread
    peer["ping_thread"] = ping_thread
    tracker_thread.start()
    ping_thread.start()
    connect_discovered_peers(peer, peer["peers"])

# Stop one peer gracefully
def stop_peer(peer):
    with peer["state_lock"]:
        if not peer["running"]:
            return

        peer["running"] = False

    peer["stop_event"].set()

    try:
        announce_peer(peer, event="stopped")
    except (OSError, RuntimeError, ValueError, TypeError) as error:
        log_event(peer["log_file"], "TRACKER_ERROR", f"Stopped announce failed: {error}")

    try:
        peer["server"].close()
    except OSError:
        pass

    with peer["connections_lock"]:
        connections = list(peer["connections"])

    for connection in connections:
        connection["connected"] = False
        release_requested_piece(connection, peer)

        try:
            connection["socket"].close()
        except OSError:
            pass

    current_thread = threading.current_thread()

    for thread in [peer["accept_thread"], peer["tracker_thread"], peer["ping_thread"]]:
        if thread is not None and thread is not current_thread:
            thread.join(timeout=2)

    log_event(peer["log_file"], "PEER_STOPPED", f"Peer on port {peer['port']} stopped")

# Run one peer until it is stopped
def run_peer(torrent_path, port, data_root=None):
    peer = start_peer(torrent_path, port, data_root=data_root)
    start_peer_tasks(peer)

    try:
        while peer["running"]:
            peer["stop_event"].wait(1)
    except KeyboardInterrupt:
        pass
    finally:
        stop_peer(peer)

    return peer

# Start one threaded peer for each torrent
def start_torrent_pool(torrent_configs):
    if not isinstance(torrent_configs, list) or not torrent_configs:
        raise ValueError("torrent configs must be a nonempty list")

    peers = []

    try:
        for config in torrent_configs:
            if not isinstance(config, dict):
                raise TypeError("torrent config must be a dictionary")

            if "torrent_path" not in config or "port" not in config:
                raise ValueError("torrent config needs torrent_path and port")

            peer = start_peer(
                config["torrent_path"],
                config["port"],
                left=config.get("left"),
                data_root=config.get("data_root")
            )
            start_peer_tasks(peer)
            peers.append(peer)

    except (OSError, RuntimeError, ValueError, TypeError):
        stop_torrent_pool(peers)
        raise

    return peers

# Stop every peer in the torrent pool
def stop_torrent_pool(peers):
    for peer in peers:
        stop_peer(peer)
