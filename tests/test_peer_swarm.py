from pathlib import Path
from tempfile import TemporaryDirectory
import time

from peer.peer import (
    announce_to_tracker,
    start_peer,
    start_peer_tasks,
    stop_peer
)


TORRENT = "metainfo/alphabet.torrent"
SOURCE = Path("shared_files/alphabet.txt")

SEEDER_PORT = 6881
FIRST_DOWNLOADER_PORT = 6882
SECOND_DOWNLOADER_PORT = 6883

seeder = None
first_downloader = None
second_downloader = None


# Wait for one peer condition
def wait_for(check, message):
    timeout = time.monotonic() + 10

    while not check():
        if time.monotonic() > timeout:
            raise TimeoutError(message)

        time.sleep(0.05)


with TemporaryDirectory(dir=".") as directory:
    first_root = Path(directory) / "peer2"
    second_root = Path(directory) / "peer3"

    try:
        print("1. Starting Peer 1 as the original seeder")

        seeder = start_peer(
            TORRENT,
            SEEDER_PORT,
            data_root="shared_files"
        )

        start_peer_tasks(seeder)

        assert seeder["left"] == 0


        print("\n2. Starting Peer 2 as a downloader")

        first_downloader = start_peer(
            TORRENT,
            FIRST_DOWNLOADER_PORT,
            data_root=first_root
        )

        start_peer_tasks(first_downloader)


        print("\n3. Waiting for Peer 2 to complete")

        wait_for(
            lambda: first_downloader["left"] == 0,
            "Peer 2 did not complete the download"
        )

        wait_for(
            lambda: first_downloader["completed_announced"],
            "Peer 2 did not announce completion"
        )

        source_data = SOURCE.read_bytes()
        first_path = first_root / "alphabet.txt"

        assert first_path.read_bytes() == source_data
        assert first_downloader["downloaded"] == len(source_data)
        assert seeder["uploaded"] == len(source_data)


        print("\n4. Stopping the original seeder")

        stop_peer(seeder)


        print("\n5. Starting Peer 3 as a downloader")

        second_downloader = start_peer(
            TORRENT,
            SECOND_DOWNLOADER_PORT,
            data_root=second_root
        )

        start_peer_tasks(second_downloader)


        print("\n6. Waiting for Peer 3 to download from Peer 2")

        wait_for(
            lambda: second_downloader["left"] == 0,
            "Peer 3 did not complete the download"
        )

        wait_for(
            lambda: second_downloader["completed_announced"],
            "Peer 3 did not announce completion"
        )


        print("\n7. Verifying the swarm result")

        second_path = second_root / "alphabet.txt"

        assert second_path.read_bytes() == source_data
        assert second_downloader["downloaded"] == len(source_data)
        assert first_downloader["uploaded"] == len(source_data)

        response = announce_to_tracker(
            tracker_url=second_downloader["tracker_url"],
            info_hash=second_downloader["info_hash"],
            peer_id=second_downloader["peer_id"],
            port=second_downloader["port"],
            uploaded=second_downloader["uploaded"],
            downloaded=second_downloader["downloaded"],
            left=second_downloader["left"]
        )

        assert response[b"complete"] == 2
        assert response[b"incomplete"] == 0


    finally:
        print("\n8. Stopping peers")

        for peer in [
            second_downloader,
            first_downloader,
            seeder
        ]:
            if peer is not None:
                stop_peer(peer)


print("\nAll peer swarm tests passed!")
