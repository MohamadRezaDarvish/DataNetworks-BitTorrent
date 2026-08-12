from urllib.request import urlopen
from urllib.parse import quote_from_bytes

from common.bencode import bendecode


TRACKER = "http://127.0.0.1:6969/announce"

INFO_HASH = b"aaaaaaaaaaaaaaaaaaaa"       # exactly 20 bytes
PEER_1 = b"peer0000000000000000"         # exactly 20 bytes
PEER_2 = b"peer1111111111111111"         # exactly 20 bytes


# Send one announce request to tracker
def announce(peer_id, port, left, event=None):
    url = (
        TRACKER
        + "?info_hash=" + quote_from_bytes(INFO_HASH)
        + "&peer_id=" + quote_from_bytes(peer_id)
        + "&port=" + str(port)
        + "&uploaded=0"
        + "&downloaded=0"
        + "&left=" + str(left)
    )

    if event:
        url += "&event=" + event

    with urlopen(url) as response:
        return bendecode(response.read())


print("1. Peer 1 starts")

response = announce(
    PEER_1,
    6881,
    1000,
    "started"
)

print(response)

assert response[b"complete"] == 0
assert response[b"incomplete"] == 1
assert response[b"peers"] == []


print("\n2. Peer 2 starts")

response = announce(
    PEER_2,
    6882,
    1000,
    "started"
)

print(response)

assert response[b"complete"] == 0
assert response[b"incomplete"] == 2
assert len(response[b"peers"]) == 1

peer = response[b"peers"][0]

assert peer[b"peer_id"] == PEER_1
assert peer[b"port"] == 6881


print("\n3. Peer 1 completes download")

response = announce(
    PEER_1,
    6881,
    0,
    "completed"
)

print(response)

assert response[b"complete"] == 1
assert response[b"incomplete"] == 1


print("\n4. Peer 2 stops")

response = announce(
    PEER_2,
    6882,
    1000,
    "stopped"
)

print(response)

assert response[b"complete"] == 1
assert response[b"incomplete"] == 0


print("\nAll tracker tests passed!")