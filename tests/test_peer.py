from peer.peer import start_peer, announce_to_tracker


TORRENT = "metainfo/alphabet.torrent"

peer1 = None
peer2 = None


try:
    print("1. Starting Peer 1 on port 6881")

    peer1 = start_peer(
        TORRENT,
        6881
    )

    print("Peer ID:", peer1["peer_id"])
    print("Info hash:", peer1["info_hash"].hex())
    print("Peers:", peer1["peers"])

    assert len(peer1["peer_id"]) == 20
    assert len(peer1["info_hash"]) == 20
    assert peer1["peers"] == []



    print("\n2. Starting Peer 2 on port 6882")

    peer2 = start_peer(
        TORRENT,
        6882
    )

    print("Peer ID:", peer2["peer_id"])
    print("Info hash:", peer2["info_hash"].hex())
    print("Peers:", peer2["peers"])

    assert len(peer2["peer_id"]) == 20
    assert len(peer2["info_hash"]) == 20

    # Both peers must be talking about the same torrent
    assert peer1["info_hash"] == peer2["info_hash"]

    # Peer 2 should discover Peer 1
    assert len(peer2["peers"]) == 1

    found_peer = peer2["peers"][0]

    assert found_peer[b"peer_id"] == peer1["peer_id"]
    assert found_peer[b"ip"] == b"127.0.0.1"
    assert found_peer[b"port"] == 6881



    print("\n3. Peer 1 asks tracker again")

    response = announce_to_tracker(
        tracker_url=peer1["tracker_url"],
        info_hash=peer1["info_hash"],
        peer_id=peer1["peer_id"],
        port=6881,
        uploaded=0,
        downloaded=0,
        left=peer1["left"]
    )

    print("Peers:", response[b"peers"])

    # Now Peer 1 should discover Peer 2
    assert len(response[b"peers"]) == 1
    assert response[b"peers"][0][b"port"] == 6882



    print("\nAll peer tracker tests passed!")


finally:
    # Tell tracker that Peer 1 is stopping
    if peer1:
        announce_to_tracker(
            tracker_url=peer1["tracker_url"],
            info_hash=peer1["info_hash"],
            peer_id=peer1["peer_id"],
            port=6881,
            uploaded=0,
            downloaded=0,
            left=peer1["left"],
            event="stopped"
        )

        peer1["server"].close()


    # Tell tracker that Peer 2 is stopping
    if peer2:
        announce_to_tracker(
            tracker_url=peer2["tracker_url"],
            info_hash=peer2["info_hash"],
            peer_id=peer2["peer_id"],
            port=6882,
            uploaded=0,
            downloaded=0,
            left=peer2["left"],
            event="stopped"
        )

        peer2["server"].close()