import struct

from common.protocol import (
    BITFIELD,
    CHOKE,
    HANDSHAKE_LENGTH,
    HAVE,
    INFO_HASH_LENGTH,
    INTERESTED,
    LENGTH_PREFIX_SIZE,
    NOT_INTERESTED,
    PEER_ID_LENGTH,
    PIECE,
    PROTOCOL,
    PROTOCOL_LENGTH,
    REQUEST,
    RESERVED_LENGTH,
    UNCHOKE,
)


# Build the BitTorrent handshake
def make_handshake(info_hash, peer_id):
    if not isinstance(info_hash, bytes) or len(info_hash) != INFO_HASH_LENGTH:
        raise ValueError(f"info_hash must be exactly {INFO_HASH_LENGTH} bytes")

    if not isinstance(peer_id, bytes) or len(peer_id) != PEER_ID_LENGTH:
        raise ValueError(f"peer_id must be exactly {PEER_ID_LENGTH} bytes")

    return (
        bytes([PROTOCOL_LENGTH])
        + PROTOCOL
        + b"\x00" * RESERVED_LENGTH
        + info_hash
        + peer_id
    )


# Parse and validate a handshake
def parse_handshake(data):
    if not isinstance(data, bytes):
        raise TypeError("handshake must be bytes")

    if len(data) != HANDSHAKE_LENGTH:
        raise ValueError(
            f"handshake must be exactly {HANDSHAKE_LENGTH} bytes"
        )

    protocol_length = data[0]

    if protocol_length != PROTOCOL_LENGTH:
        raise ValueError("invalid protocol length")

    protocol_start = 1
    protocol_end = protocol_start + PROTOCOL_LENGTH

    reserved_start = protocol_end
    reserved_end = reserved_start + RESERVED_LENGTH

    info_hash_start = reserved_end
    info_hash_end = info_hash_start + INFO_HASH_LENGTH

    peer_id_start = info_hash_end
    peer_id_end = peer_id_start + PEER_ID_LENGTH

    protocol = data[protocol_start:protocol_end]
    reserved = data[reserved_start:reserved_end]
    info_hash = data[info_hash_start:info_hash_end]
    peer_id = data[peer_id_start:peer_id_end]

    if protocol != PROTOCOL:
        raise ValueError("invalid protocol name")

    return {
        "protocol": protocol,
        "reserved": reserved,
        "info_hash": info_hash,
        "peer_id": peer_id
    }


# Build a normal length-prefixed peer message
def make_message(message_id, payload=b""):
    if not isinstance(payload, bytes):
        raise TypeError("payload must be bytes")

    if not isinstance(message_id, int):
        raise TypeError("message_id must be an integer")

    if message_id < 0 or message_id > 255:
        raise ValueError("message_id must be between 0 and 255")

    length = 1 + len(payload)

    return (
        struct.pack(">I", length)
        + bytes([message_id])
        + payload
    )


# Keep connection alive without sending a command
def make_keep_alive():
    return struct.pack(">I", 0)


# Tell peer it cannot request pieces right now
def make_choke():
    return make_message(CHOKE)


# Tell peer it may request pieces
def make_unchoke():
    return make_message(UNCHOKE)


# Tell peer it has pieces we want
def make_interested():
    return make_message(INTERESTED)


# Tell peer it currently has nothing we want
def make_not_interested():
    return make_message(NOT_INTERESTED)


# Tell peer we now have one piece
def make_have(piece_index):
    if piece_index < 0:
        raise ValueError("piece index cannot be negative")

    payload = struct.pack(">I", piece_index)

    return make_message(HAVE, payload)


# Send our piece-availability bitfield
def make_bitfield(bitfield):
    if not isinstance(bitfield, bytes):
        raise TypeError("bitfield must be bytes")

    return make_message(BITFIELD, bitfield)


# Request part of a piece
def make_request(piece_index, begin, length):
    if piece_index < 0:
        raise ValueError("piece index cannot be negative")

    if begin < 0:
        raise ValueError("begin cannot be negative")

    if length <= 0:
        raise ValueError("length must be positive")

    payload = struct.pack(
        ">III",
        piece_index,
        begin,
        length
    )

    return make_message(REQUEST, payload)


# Send requested piece data
def make_piece(piece_index, begin, block):
    if piece_index < 0:
        raise ValueError("piece index cannot be negative")

    if begin < 0:
        raise ValueError("begin cannot be negative")

    if not isinstance(block, bytes):
        raise TypeError("block must be bytes")

    payload = (
        struct.pack(">II", piece_index, begin)
        + block
    )

    return make_message(PIECE, payload)


# Parse one complete length-prefixed peer message
def parse_message(data):
    if not isinstance(data, bytes):
        raise TypeError("message must be bytes")

    if len(data) < LENGTH_PREFIX_SIZE:
        raise ValueError("message is too short")

    length = struct.unpack(
        ">I",
        data[:LENGTH_PREFIX_SIZE]
    )[0]

    if length == 0:
        if len(data) != LENGTH_PREFIX_SIZE:
            raise ValueError("invalid keep-alive message")

        return {
            "type": "keep_alive"
        }

    expected_size = LENGTH_PREFIX_SIZE + length

    if len(data) != expected_size:
        raise ValueError("message length does not match prefix")

    message_id = data[LENGTH_PREFIX_SIZE]
    payload = data[LENGTH_PREFIX_SIZE + 1:]

    if message_id == CHOKE:
        if length != 1:
            raise ValueError("invalid choke message")

        return {
            "type": "choke"
        }

    if message_id == UNCHOKE:
        if length != 1:
            raise ValueError("invalid unchoke message")

        return {
            "type": "unchoke"
        }

    if message_id == INTERESTED:
        if length != 1:
            raise ValueError("invalid interested message")

        return {
            "type": "interested"
        }

    if message_id == NOT_INTERESTED:
        if length != 1:
            raise ValueError("invalid not interested message")

        return {
            "type": "not_interested"
        }

    if message_id == HAVE:
        if length != 5:
            raise ValueError("invalid have message")

        piece_index = struct.unpack(">I", payload)[0]

        return {
            "type": "have",
            "piece_index": piece_index
        }

    if message_id == BITFIELD:
        return {
            "type": "bitfield",
            "bitfield": payload
        }

    if message_id == REQUEST:
        if length != 13:
            raise ValueError("invalid request message")

        piece_index, begin, block_length = struct.unpack(
            ">III",
            payload
        )

        return {
            "type": "request",
            "piece_index": piece_index,
            "begin": begin,
            "length": block_length
        }

    if message_id == PIECE:
        if length < 9:
            raise ValueError("invalid piece message")

        piece_index, begin = struct.unpack(
            ">II",
            payload[:8]
        )

        block = payload[8:]

        return {
            "type": "piece",
            "piece_index": piece_index,
            "begin": begin,
            "block": block
        }

    return {
        "type": "unknown",
        "id": message_id,
        "payload": payload
    }