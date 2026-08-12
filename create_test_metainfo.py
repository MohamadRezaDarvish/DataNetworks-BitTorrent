from pathlib import Path
import hashlib

from common.bencode import bencode

SOURCE = Path("shared_files/alphabet.txt")
OUTPUT = Path("metainfo/alphabet.torrent")

TRACKER_URL = b"http://127.0.0.1:8080/announce"
PIECE_LENGTH = 10


data = SOURCE.read_bytes()

piece_hashes = b"".join(
    hashlib.sha1(data[i:i + PIECE_LENGTH]).digest()
    for i in range(0, len(data), PIECE_LENGTH)
)

info = {
    b"length": len(data),
    b"name": SOURCE.name.encode("utf-8"),
    b"piece length": PIECE_LENGTH,
    b"pieces": piece_hashes,
}

metainfo = {
    b"announce": TRACKER_URL,
    b"info": info,
}

OUTPUT.parent.mkdir(exist_ok=True)
OUTPUT.write_bytes(bencode(metainfo))

info_hash = hashlib.sha1(bencode(info)).hexdigest()

print(f"created: {OUTPUT}")
print(f"file size: {len(data)} bytes")
print(f"piece length: {PIECE_LENGTH} bytes")
print(f"piece count: {(len(data) + PIECE_LENGTH - 1) // PIECE_LENGTH}")
print(f"pieces field length: {len(piece_hashes)} bytes")
print(f"info_hash: {info_hash}")
