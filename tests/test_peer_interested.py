import time

from peer.messages import make_interested
from peer.peer import (
    announce_to_tracker,
    connect_and_handshake,
    receive_peer_message,
    send_peer_message,
    start_peer
)


TORRENT = "metainfo/alphabet.torrent"

peer1 = None
peer2 = None
connection = None


try:
    print("1. Starting Peer 1")

    peer1 = start_peer(
        TORRENT,
        6881,
        left=0
    )

    print("\n2. Starting Peer 2")

    peer2 = start_peer(
        TORRENT,
        6882
    )

    print("\n3. Peer 2 connects to Peer 1")

    connection = connect_and_handshake(
        ip="127.0.0.1",
        port=6881,
        info_hash=peer2["info_hash"],
        peer_id=peer2["peer_id"],
        log_file="logs/peer_6882.log"
    )

    assert connection is not None

    print("\n4. Peer 2 sends INTERESTED")

    send_peer_message(
        connection["socket"],
        make_interested()
    )

    connection["am_interested"] = True

    print("\n5. Peer 2 waits for UNCHOKE")

    response = receive_peer_message(
        connection["socket"]
    )

    print("Received:", response)

    assert response["type"] == "unchoke"

    connection["am_choked"] = False

    timeout = time.time() + 3

    while not peer1["connections"]:
        if time.time() > timeout:
            raise TimeoutError(
                "Peer 1 did not record the connection"
            )

        time.sleep(0.05)

    incoming = peer1["connections"][0]

    assert incoming["remote_interested"] is True
    assert incoming["am_choking"] is False
    assert connection["am_interested"] is True
    assert connection["am_choked"] is False

    print("\nAll interested/unchoke tests passed!")


finally:
    if connection:
        connection["socket"].close()

    for peer in (peer1, peer2):
        if not peer:
            continue

        for item in peer["connections"]:
            try:
                item["socket"].close()
            except OSError:
                pass

        try:
            announce_to_tracker(
                peer["tracker_url"],
                peer["info_hash"],
                peer["peer_id"],
                peer["port"],
                peer["uploaded"],
                peer["downloaded"],
                peer["left"],
                "stopped"
            )
        except Exception:
            pass

        peer["server"].close()