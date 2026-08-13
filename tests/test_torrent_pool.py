from pathlib import Path
from tempfile import TemporaryDirectory
import hashlib

from common.bencode import bencode
from peer.peer import (
    announce_to_tracker,
    start_torrent_pool,
    stop_torrent_pool
)


TRACKER_URL = b"http://127.0.0.1:6969/announce"
FIRST_PORT = 6891
SECOND_PORT = 6892


# Create one temporary torrent
def create_torrent(source, torrent_path, piece_length):
    data = source.read_bytes()

    pieces = b"".join(
        hashlib.sha1(
            data[index:index + piece_length]
        ).digest()
        for index in range(0, len(data), piece_length)
    )

    info = {
        b"length": len(data),
        b"name": source.name.encode("utf-8"),
        b"piece length": piece_length,
        b"pieces": pieces
    }

    metainfo = {
        b"announce": TRACKER_URL,
        b"info": info
    }

    torrent_path.write_bytes(
        bencode(metainfo)
    )


peers = []


with TemporaryDirectory(dir=".") as directory:
    root = Path(directory)
    data_root = root / "shared_files"
    torrent_root = root / "metainfo"

    data_root.mkdir()
    torrent_root.mkdir()

    first_source = data_root / "first.txt"
    second_source = data_root / "second.txt"

    first_source.write_bytes(b"ABCDEFGHIJKL")
    second_source.write_bytes(b"1234567890")

    first_torrent = torrent_root / "first.torrent"
    second_torrent = torrent_root / "second.torrent"

    create_torrent(
        first_source,
        first_torrent,
        5
    )

    create_torrent(
        second_source,
        second_torrent,
        4
    )

    configs = [
        {
            "torrent_path": first_torrent,
            "port": FIRST_PORT,
            "data_root": data_root
        },
        {
            "torrent_path": second_torrent,
            "port": SECOND_PORT,
            "data_root": data_root
        }
    ]

    try:
        print("1. Starting the two-torrent thread pool")

        peers = start_torrent_pool(configs)

        assert len(peers) == 2


        print("\n2. Checking both torrent peers")

        assert peers[0]["port"] == FIRST_PORT
        assert peers[1]["port"] == SECOND_PORT
        assert peers[0]["info_hash"] != peers[1]["info_hash"]

        for peer in peers:
            assert peer["running"] is True
            assert peer["tasks_started"] is True
            assert peer["left"] == 0
            assert peer["completed_announced"] is True
            assert peer["accept_thread"].is_alive()
            assert peer["tracker_thread"].is_alive()
            assert peer["ping_thread"].is_alive()


        print("\n3. Checking separate tracker swarms")

        for peer in peers:
            response = announce_to_tracker(
                tracker_url=peer["tracker_url"],
                info_hash=peer["info_hash"],
                peer_id=peer["peer_id"],
                port=peer["port"],
                uploaded=peer["uploaded"],
                downloaded=peer["downloaded"],
                left=peer["left"]
            )

            assert response[b"complete"] == 1
            assert response[b"incomplete"] == 0
            assert response[b"peers"] == []


    finally:
        print("\n4. Stopping the torrent pool")

        stop_torrent_pool(peers)


for peer in peers:
    assert peer["running"] is False
    assert peer["stop_event"].is_set()


print("\nAll torrent pool tests passed!")
