from pathlib import Path
import hashlib

from common.bencode import bencode


SHARED_DIRECTORY = Path("shared_files")
METAINFO_DIRECTORY = Path("metainfo")

TRACKER_URL = b"http://127.0.0.1:6969/announce"

SMALL_FILE_LIMIT = 100
SMALL_PIECE_LENGTH = 10
DEFAULT_PIECE_LENGTH = 256 * 1024


# Choose the piece length for one file
def get_piece_length(file_size):
    if file_size <= SMALL_FILE_LIMIT:
        return SMALL_PIECE_LENGTH

    return DEFAULT_PIECE_LENGTH


# Create one torrent from one shared file
def create_torrent(source):
    data = source.read_bytes()

    if not data:
        raise ValueError(f"Shared file is empty: {source}")

    piece_length = get_piece_length(len(data))

    piece_hashes = b"".join(
        hashlib.sha1(
            data[index:index + piece_length]
        ).digest()
        for index in range(0, len(data), piece_length)
    )

    info = {
        b"length": len(data),
        b"name": source.name.encode("utf-8"),
        b"piece length": piece_length,
        b"pieces": piece_hashes
    }

    metainfo = {
        b"announce": TRACKER_URL,
        b"info": info
    }

    output = METAINFO_DIRECTORY / f"{source.stem}.torrent"

    output.write_bytes(
        bencode(metainfo)
    )

    info_hash = hashlib.sha1(
        bencode(info)
    ).hexdigest()

    piece_count = (
        len(data) + piece_length - 1
    ) // piece_length

    print(f"Created: {output}")
    print(f"Source: {source}")
    print(f"File size: {len(data)} bytes")
    print(f"Piece length: {piece_length} bytes")
    print(f"Piece count: {piece_count}")
    print(f"Info hash: {info_hash}")
    print()

    return output


# Create torrents for all shared files
def generate_torrents():
    if not SHARED_DIRECTORY.is_dir():
        raise FileNotFoundError("shared_files directory was not found")

    sources = sorted(
        source
        for source in SHARED_DIRECTORY.iterdir()
        if source.is_file() and not source.name.startswith(".")
    )

    if not sources:
        raise ValueError("shared_files contains no files")

    METAINFO_DIRECTORY.mkdir(exist_ok=True)

    outputs = []

    for source in sources:
        outputs.append(
            create_torrent(source)
        )

    return outputs


generate_torrents()
