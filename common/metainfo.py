import hashlib
from pathlib import Path

from common.bencode import bencode, bendecode


# Read and decode a .torrent file
def load_torrent(torrent_path):
    data = Path(torrent_path).read_bytes()
    metainfo = bendecode(data)

    if not isinstance(metainfo, dict):
        raise TypeError("torrent must contain a dictionary")

    if b"announce" not in metainfo:
        raise ValueError("torrent has no announce field")

    if b"info" not in metainfo:
        raise ValueError("torrent has no info field")

    return metainfo


# Calculate the 20-byte torrent ID
def calculate_info_hash(info):
    return hashlib.sha1(
        bencode(info)
    ).digest()


# Get total torrent size
def get_total_size(info):
    if b"length" in info:
        return info[b"length"]

    if b"files" in info:
        total = 0

        for file_info in info[b"files"]:
            total += file_info[b"length"]

        return total

    raise ValueError("torrent has no file length information")


# Get tracker URL from torrent
def get_tracker_url(metainfo):
    tracker = metainfo[b"announce"]

    if isinstance(tracker, list):
        if not tracker:
            raise ValueError("announce list is empty")

        tracker = tracker[0]

    if isinstance(tracker, bytes):
        tracker = tracker.decode("utf-8")

    if not isinstance(tracker, str):
        raise ValueError("invalid tracker URL")

    return tracker
