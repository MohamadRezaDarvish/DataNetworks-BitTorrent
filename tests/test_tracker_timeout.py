import time
import threading

from http.server import ThreadingHTTPServer
from urllib.request import urlopen
from urllib.parse import quote_from_bytes

from common.bencode import bendecode
from tracker import tracker


INFO_HASH = b"aaaaaaaaaaaaaaaaaaaa"

PEER_1 = b"peer0000000000000000"
PEER_2 = b"peer1111111111111111"


# Start one tracker for this test
def start_test_tracker():
    tracker.PEER_TIMEOUT = 1

    with tracker.lock:
        tracker.swarms.clear()

    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        tracker.TrackerHandler
    )

    thread = threading.Thread(
        target=server.serve_forever,
        daemon=True
    )

    thread.start()

    return server, thread


# Send tracker announce
def announce(tracker_url, peer_id, port, left, event=None):
    url = (
        tracker_url
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


original_timeout = tracker.PEER_TIMEOUT
server, thread = start_test_tracker()

host, port = server.server_address
tracker_url = f"http://{host}:{port}/announce"


try:
    print("1. Peer 1 starts")

    response = announce(
        tracker_url,
        PEER_1,
        6881,
        1000,
        "started"
    )

    print(response)

    assert response[b"incomplete"] == 1


    print("\n2. Waiting for Peer 1 to become stale...")

    time.sleep(1.2)


    print("\n3. Peer 2 starts")

    response = announce(
        tracker_url,
        PEER_2,
        6882,
        1000,
        "started"
    )

    print(response)


    assert response[b"incomplete"] == 1
    assert response[b"complete"] == 0
    assert response[b"peers"] == []


    print("\nStale peer timeout test passed!")


finally:
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)
    tracker.PEER_TIMEOUT = original_timeout

    with tracker.lock:
        tracker.swarms.clear()
