from peer.messages import (
    make_handshake,
    parse_handshake,
    make_keep_alive,
    make_choke,
    make_unchoke,
    make_interested,
    make_not_interested,
    make_have,
    make_bitfield,
    make_request,
    make_piece,
    parse_message
)


INFO_HASH = b"12345678901234567890"
PEER_ID = b"-DN0001-123456789012"


print("1. Testing handshake")

handshake = make_handshake(INFO_HASH, PEER_ID)

assert isinstance(handshake, bytes)
assert len(handshake) == 68

parsed = parse_handshake(handshake)

assert parsed["protocol"] == b"BitTorrent protocol"
assert parsed["info_hash"] == INFO_HASH
assert parsed["peer_id"] == PEER_ID

print("   Handshake passed")


print("\n2. Testing keep-alive")

message = make_keep_alive()

assert message == b"\x00\x00\x00\x00"

parsed = parse_message(message)

assert parsed["type"] == "keep_alive"

print("   Keep-alive passed")


print("\n3. Testing choke")

message = make_choke()

assert message == b"\x00\x00\x00\x01\x00"
assert parse_message(message)["type"] == "choke"

print("   Choke passed")


print("\n4. Testing unchoke")

message = make_unchoke()

assert message == b"\x00\x00\x00\x01\x01"
assert parse_message(message)["type"] == "unchoke"

print("   Unchoke passed")


print("\n5. Testing interested")

message = make_interested()

assert message == b"\x00\x00\x00\x01\x02"
assert parse_message(message)["type"] == "interested"

print("   Interested passed")


print("\n6. Testing not interested")

message = make_not_interested()

assert message == b"\x00\x00\x00\x01\x03"
assert parse_message(message)["type"] == "not_interested"

print("   Not interested passed")


print("\n7. Testing have")

message = make_have(3)

parsed = parse_message(message)

assert parsed["type"] == "have"
assert parsed["piece_index"] == 3

print("   HAVE passed")


print("\n8. Testing bitfield")

bitfield = b"\xA0"

message = make_bitfield(bitfield)

parsed = parse_message(message)

assert parsed["type"] == "bitfield"
assert parsed["bitfield"] == bitfield

print("   Bitfield passed")


print("\n9. Testing request")

message = make_request(
    piece_index=2,
    begin=0,
    length=16384
)

parsed = parse_message(message)

assert parsed["type"] == "request"
assert parsed["piece_index"] == 2
assert parsed["begin"] == 0
assert parsed["length"] == 16384

print("   Request passed")


print("\n10. Testing piece")

block = b"Hello BitTorrent"

message = make_piece(
    piece_index=2,
    begin=0,
    block=block
)

parsed = parse_message(message)

assert parsed["type"] == "piece"
assert parsed["piece_index"] == 2
assert parsed["begin"] == 0
assert parsed["block"] == block

print("   Piece passed")


print("\n11. Testing invalid handshake")

try:
    parse_handshake(b"bad")
    assert False
except ValueError:
    pass

print("   Invalid handshake passed")


print("\n12. Testing invalid request")

try:
    make_request(
        piece_index=1,
        begin=0,
        length=-1
    )
    assert False
except ValueError:
    pass

print("   Invalid request passed")


print("\nAll message tests passed!")