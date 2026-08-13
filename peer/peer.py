from pathlib import Path
import hashlib
import secrets
import socket
import struct
import threading
from urllib.request import urlopen
from urllib.parse import quote_from_bytes

from common.bencode import bencode, bendecode
from common.logger import log_event
from common.protocol import HANDSHAKE_LENGTH, LENGTH_PREFIX_SIZE
from peer.messages import (
    make_bitfield,
    make_handshake,
    make_have,
    make_interested,
    make_not_interested,
    make_unchoke,
    parse_handshake,
    parse_message
)
from peer.piece_manager import (
    create_piece_manager,
    get_bitfield,
    get_bytes_left,
    get_missing_pieces,
    get_piece_count,
    have_piece
)


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
    connections,
    piece_manager
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
                connections,
                piece_manager
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


# Send one normal peer message
def send_peer_message(sock, message):
    if not isinstance(message, bytes):
        raise TypeError("message must be bytes")

    sock.sendall(message)


# Receive one complete normal peer message
def receive_peer_message(sock):
    length_data = recv_exact(
        sock,
        LENGTH_PREFIX_SIZE
    )

    length = struct.unpack(
        ">I",
        length_data
    )[0]

    if length == 0:
        return parse_message(length_data)

    message_data = recv_exact(
        sock,
        length
    )

    return parse_message(
        length_data + message_data
    )


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
        "send_lock": threading.Lock()
    }


# Send one message without mixing concurrent writes
def send_connection_message(connection, message):
    with connection["send_lock"]:
        send_peer_message(
            connection["socket"],
            message
        )


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
def send_local_bitfield(
    connection,
    piece_manager,
    log_file
):
    bitfield = get_bitfield(piece_manager)

    send_connection_message(
        connection,
        make_bitfield(bitfield)
    )

    connection["sent_bitfield"] = True

    log_event(
        log_file,
        "BITFIELD_SENT",
        f"Sent bitfield {bitfield.hex()} to {connection['address']}"
    )


# Update whether this peer is interested in a remote peer
def update_interest(
    connection,
    piece_manager,
    log_file
):
    missing_pieces = set(
        get_missing_pieces(piece_manager)
    )
    interested = bool(
        missing_pieces & connection["remote_pieces"]
    )

    if interested == connection["am_interested"]:
        return

    if interested:
        message = make_interested()
        event_type = "INTERESTED_SENT"
        description = (
            f"Interested in pieces from {connection['address']}"
        )
    else:
        message = make_not_interested()
        event_type = "NOT_INTERESTED_SENT"
        description = (
            f"No longer interested in {connection['address']}"
        )

    send_connection_message(
        connection,
        message
    )

    connection["am_interested"] = interested

    log_event(
        log_file,
        event_type,
        description
    )


# Handle one parsed message from a connected peer
def handle_received_message(
    connection,
    piece_manager,
    log_file,
    message
):
    message_type = message["type"]
    address = connection["address"]

    if message_type == "keep_alive":
        return

    if message_type == "bitfield":
        if piece_manager is None:
            raise ValueError("cannot read bitfield without piece manager")

        if connection["received_bitfield"]:
            raise ValueError("peer sent more than one bitfield")

        remote_pieces = decode_bitfield(
            piece_manager,
            message["bitfield"]
        )

        connection["remote_bitfield"] = message["bitfield"]
        connection["remote_pieces"] = remote_pieces
        connection["received_bitfield"] = True

        log_event(
            log_file,
            "BITFIELD_RECEIVED",
            f"Peer {address} has pieces {sorted(remote_pieces)}"
        )

        if not connection["sent_bitfield"]:
            send_local_bitfield(
                connection,
                piece_manager,
                log_file
            )

        update_interest(
            connection,
            piece_manager,
            log_file
        )

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
            remote_bitfield = bytearray(
                connection["remote_bitfield"]
            )
        else:
            remote_bitfield = bytearray(bitfield_size)

        byte_index = piece_index // 8
        bit_index = 7 - piece_index % 8
        remote_bitfield[byte_index] |= 1 << bit_index
        connection["remote_bitfield"] = bytes(remote_bitfield)

        log_event(
            log_file,
            "HAVE_RECEIVED",
            f"Peer {address} now has piece {piece_index}"
        )

        update_interest(
            connection,
            piece_manager,
            log_file
        )

        return

    if message_type == "interested":
        connection["remote_interested"] = True

        log_event(
            log_file,
            "INTERESTED_RECEIVED",
            f"Peer {address} is interested"
        )

        if connection["am_choking"]:
            send_connection_message(
                connection,
                make_unchoke()
            )

            connection["am_choking"] = False

            log_event(
                log_file,
                "UNCHOKE_SENT",
                f"Unchoked peer {address}"
            )

        return

    if message_type == "not_interested":
        connection["remote_interested"] = False

        log_event(
            log_file,
            "NOT_INTERESTED_RECEIVED",
            f"Peer {address} is not interested"
        )

        return

    if message_type == "choke":
        connection["am_choked"] = True

        log_event(
            log_file,
            "CHOKE_RECEIVED",
            f"Peer {address} choked us"
        )

        return

    if message_type == "unchoke":
        connection["am_choked"] = False

        log_event(
            log_file,
            "UNCHOKE_RECEIVED",
            f"Peer {address} unchoked us"
        )

        return

    log_event(
        log_file,
        "PEER_MESSAGE",
        f"Received {message_type} from {address}"
    )


# Handle messages received from one connected peer
def handle_peer_messages(
    sock,
    address,
    connection,
    log_file,
    piece_manager=None
):
    while True:
        try:
            message = receive_peer_message(sock)

            handle_received_message(
                connection,
                piece_manager,
                log_file,
                message
            )

        except socket.timeout:
            continue

        except (
            ConnectionError,
            ValueError,
            TypeError,
            IndexError,
            OSError
        ) as error:
            connection["connected"] = False

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
    piece_manager
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

        connection = create_connection_state(
            sock,
            address,
            remote_handshake["peer_id"]
        )

        connections.append(connection)

        log_event(
            log_file,
            "PEER_HANDSHAKE",
            f"Handshake completed with {address}"
        )

        handle_peer_messages(
            sock,
            address,
            connection,
            log_file,
            piece_manager
        )

    except (
        ConnectionError,
        ValueError,
        TypeError,
        socket.timeout,
        OSError
    ) as error:
        log_event(
            log_file,
            "PEER_CONNECTION_FAILED",
            f"Connection from {address} failed: {error}"
        )

        try:
            sock.close()
        except OSError:
            pass


# Connect to another peer and exchange handshakes
def connect_and_handshake(
    ip,
    port,
    info_hash,
    peer_id,
    log_file,
    piece_manager=None
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

        connection = create_connection_state(
            sock,
            (ip, port),
            remote_handshake["peer_id"]
        )

        log_event(
            log_file,
            "PEER_HANDSHAKE",
            f"Handshake completed with {(ip, port)}"
        )

        if piece_manager is not None:
            send_local_bitfield(
                connection,
                piece_manager,
                log_file
            )

        return connection

    except (
        ConnectionError,
        ValueError,
        TypeError,
        socket.timeout,
        OSError
    ) as error:
        log_event(
            log_file,
            "PEER_CONNECTION_FAILED",
            f"Connection to {(ip, port)} failed: {error}"
        )

        if sock:
            sock.close()

        return None


# Start the message reader for one outgoing connection
def start_peer_message_thread(
    connection,
    piece_manager,
    log_file
):
    thread = threading.Thread(
        target=handle_peer_messages,
        args=(
            connection["socket"],
            connection["address"],
            connection,
            log_file,
            piece_manager
        ),
        daemon=True
    )

    connection["message_thread"] = thread
    thread.start()

    return thread


# Connect one running peer to another peer
def connect_peer(peer, ip, port):
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

    peer["connections"].append(connection)

    start_peer_message_thread(
        connection,
        peer["piece_manager"],
        peer["log_file"]
    )

    return connection


# Tell every connected peer about one completed piece
def send_have_to_peers(
    connections,
    piece_manager,
    piece_index,
    log_file
):
    if not have_piece(piece_manager, piece_index):
        raise ValueError("cannot announce a missing piece")

    message = make_have(piece_index)
    sent_count = 0

    for connection in list(connections):
        if not connection["connected"]:
            continue

        try:
            send_connection_message(
                connection,
                message
            )

            sent_count += 1

            log_event(
                log_file,
                "HAVE_SENT",
                f"Sent piece {piece_index} to {connection['address']}"
            )

            update_interest(
                connection,
                piece_manager,
                log_file
            )

        except OSError as error:
            connection["connected"] = False

            log_event(
                log_file,
                "PEER_DISCONNECTED",
                f"Could not send HAVE to {connection['address']}: {error}"
            )

    return sent_count



# Prepare one peer and announce it to the tracker
def start_peer(
    torrent_path,
    port,
    left=None,
    data_root=None
):
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

    piece_manager = create_piece_manager(
        info,
        data_root
    )
    left = get_bytes_left(piece_manager)

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
            connections,
            piece_manager
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
        "data_root": Path(data_root),
        "piece_manager": piece_manager,
        "log_file": log_file,
        "server": server,
        "peers": peers,
        "connections": connections,
        "accept_thread": accept_thread,
        "interval": tracker_response[b"interval"]
    }
