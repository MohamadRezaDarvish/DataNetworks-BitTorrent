import time

from peer.peer import (
    start_peer,
    connect_and_handshake,
    announce_to_tracker
)


TORRENT = "metainfo/alphabet.torrent"

peer1 = None
peer2 = None
outgoing_connection = None


try:
    print("1. Starting Peer 1 on port 6881")

    peer1 = start_peer(
        TORRENT,
        6881
    )

    print("   Peer 1 ID:", peer1["peer_id"])


    print("\n2. Starting Peer 2 on port 6882")

    peer2 = start_peer(
        TORRENT,
        6882
    )

    print("   Peer 2 ID:", peer2["peer_id"])


    print("\n3. Peer 2 connects to Peer 1")

    outgoing_connection = connect_and_handshake(
        ip="127.0.0.1",
        port=6881,
        info_hash=peer2["info_hash"],
        peer_id=peer2["peer_id"],
        log_file="logs/peer_6882.log"
    )

    assert outgoing_connection is not None

    print("   Peer 2 received Peer 1 handshake")


    print("\n4. Checking Peer 1 identity")

    assert outgoing_connection["peer_id"] == peer1["peer_id"]

    print("   Peer 2 correctly recognized Peer 1")


    print("\n5. Waiting for Peer 1 accept thread")

    timeout = time.time() + 3

    while len(peer1["connections"]) == 0:
        if time.time() > timeout:
            raise TimeoutError(
                "Peer 1 did not record the incoming connection"
            )

        time.sleep(0.05)


    incoming_connection = peer1["connections"][0]

    assert incoming_connection["peer_id"] == peer2["peer_id"]

    print("   Peer 1 correctly recognized Peer 2")


    print("\n6. Checking torrent")

    assert peer1["info_hash"] == peer2["info_hash"]

    print("   Both peers are using the same torrent")


    print("\nAll TCP handshake tests passed!")


finally:
    # Close outgoing connection
    if outgoing_connection:
        outgoing_connection["socket"].close()


    # Close Peer 1 incoming connections
    if peer1:
        for connection in peer1["connections"]:
            connection["socket"].close()


    # Tell tracker Peer 1 stopped
    if peer1:
        try:
            announce_to_tracker(
                tracker_url=peer1["tracker_url"],
                info_hash=peer1["info_hash"],
                peer_id=peer1["peer_id"],
                port=peer1["port"],
                uploaded=peer1["uploaded"],
                downloaded=peer1["downloaded"],
                left=peer1["left"],
                event="stopped"
            )
        except Exception:
            pass

        peer1["server"].close()


    # Tell tracker Peer 2 stopped
    if peer2:
        try:
            announce_to_tracker(
                tracker_url=peer2["tracker_url"],
                info_hash=peer2["info_hash"],
                peer_id=peer2["peer_id"],
                port=peer2["port"],
                uploaded=peer2["uploaded"],
                downloaded=peer2["downloaded"],
                left=peer2["left"],
                event="stopped"
            )
        except Exception:
            pass

        peer2["server"].close()