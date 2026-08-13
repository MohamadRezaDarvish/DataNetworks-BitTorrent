from pathlib import Path
from tempfile import TemporaryDirectory
import time

from peer.peer import (
    announce_to_tracker,
    start_peer,
    start_peer_tasks,
    stop_peer
)
from peer.piece_manager import get_piece_count


TORRENT = "metainfo/alphabet.torrent"
SOURCE = Path("shared_files/alphabet.txt")

SEEDER_PORT = 6881
DOWNLOADER_PORT = 6882

seeder = None
downloader = None


# Wait for one peer condition
def wait_for(check, message):
    timeout = time.monotonic() + 10

    while not check():
        if time.monotonic() > timeout:
            raise TimeoutError(message)

        time.sleep(0.05)


with TemporaryDirectory(dir=".") as directory:
    download_root = Path(directory) / "downloads"

    try:
        print("1. Starting Peer 1 as seeder")

        seeder = start_peer(
            TORRENT,
            SEEDER_PORT,
            left=0,
            data_root="shared_files"
        )

        start_peer_tasks(seeder)

        assert seeder["left"] == 0


        print("\n2. Starting Peer 2 as downloader")

        downloader = start_peer(
            TORRENT,
            DOWNLOADER_PORT,
            data_root=download_root
        )

        start_peer_tasks(downloader)

        assert downloader["left"] == SOURCE.stat().st_size


        print("\n3. Waiting for the complete file download")

        wait_for(
            lambda: downloader["left"] == 0,
            "Peer 2 did not complete the download"
        )

        wait_for(
            lambda: downloader["completed_announced"],
            "Peer 2 did not announce completion"
        )


        print("\n4. Verifying downloaded data and counters")

        source_data = SOURCE.read_bytes()
        downloaded_path = download_root / "alphabet.txt"

        assert downloaded_path.exists()
        assert downloaded_path.read_bytes() == source_data
        assert downloader["downloaded"] == len(source_data)
        assert downloader["left"] == 0
        assert seeder["uploaded"] == len(source_data)


        print("\n5. Verifying HAVE messages")

        expected_pieces = set(
            range(get_piece_count(downloader["piece_manager"]))
        )

        wait_for(
            lambda: any(
                connection["peer_id"] == downloader["peer_id"]
                and connection["remote_pieces"] == expected_pieces
                for connection in seeder["connections"]
            ),
            "Peer 1 did not receive all HAVE messages"
        )


        print("\n6. Checking tracker completion state")

        response = announce_to_tracker(
            tracker_url=downloader["tracker_url"],
            info_hash=downloader["info_hash"],
            peer_id=downloader["peer_id"],
            port=downloader["port"],
            uploaded=downloader["uploaded"],
            downloaded=downloader["downloaded"],
            left=downloader["left"]
        )

        assert response[b"complete"] == 2
        assert response[b"incomplete"] == 0


    finally:
        print("\n7. Stopping peers")

        if downloader:
            stop_peer(downloader)

        if seeder:
            stop_peer(seeder)


print("\nAll peer download tests passed!")
