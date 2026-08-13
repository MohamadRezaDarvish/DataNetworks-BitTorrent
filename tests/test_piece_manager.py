from pathlib import Path
from tempfile import TemporaryDirectory
import hashlib

from common.bencode import bendecode
from peer.piece_manager import (
    create_piece_manager,
    get_available_pieces,
    get_bitfield,
    get_bytes_left,
    get_missing_pieces,
    get_piece_count,
    get_piece_hash,
    get_piece_size,
    have_piece,
    is_complete,
    read_piece,
    read_piece_block,
    refresh_pieces,
    save_piece,
    verify_piece
)


TORRENT = Path("metainfo/alphabet.torrent")
SOURCE = Path("shared_files/alphabet.txt")


print("1. Loading the alphabet torrent")

metainfo = bendecode(TORRENT.read_bytes())
info = metainfo[b"info"]
source_data = SOURCE.read_bytes()

pieces = [
    source_data[index:index + info[b"piece length"]]
    for index in range(0, len(source_data), info[b"piece length"])
]

assert pieces == [
    b"ABCDEFGHIJ",
    b"KLMNOPQRST",
    b"UVWXYZ"
]


print("\n2. Scanning complete seeder data")

seeder = create_piece_manager(
    info,
    "shared_files"
)

assert get_piece_count(seeder) == 3
assert get_piece_size(seeder, 0) == 10
assert get_piece_size(seeder, 1) == 10
assert get_piece_size(seeder, 2) == 6
assert get_available_pieces(seeder) == [0, 1, 2]
assert get_missing_pieces(seeder) == []
assert get_bitfield(seeder) == b"\xe0"
assert get_bytes_left(seeder) == 0
assert is_complete(seeder) is True

for piece_index, piece_data in enumerate(pieces):
    assert have_piece(seeder, piece_index) is True
    assert get_piece_hash(seeder, piece_index) == hashlib.sha1(
        piece_data
    ).digest()


print("\n3. Verifying and reading pieces")

assert verify_piece(seeder, 0, pieces[0]) is True
assert verify_piece(seeder, 0, b"X" + pieces[0][1:]) is False
assert verify_piece(seeder, 2, pieces[2] + b"X") is False
assert read_piece(seeder, 0) == pieces[0]
assert read_piece(seeder, 2) == pieces[2]
assert read_piece_block(seeder, 1, 2, 4) == b"MNOP"


print("\n4. Saving verified downloader pieces")

with TemporaryDirectory() as directory:
    download_root = Path(directory) / "downloads"
    downloader = create_piece_manager(
        info,
        download_root
    )

    assert get_available_pieces(downloader) == []
    assert get_missing_pieces(downloader) == [0, 1, 2]
    assert get_bitfield(downloader) == b"\x00"
    assert get_bytes_left(downloader) == 26
    assert is_complete(downloader) is False

    assert save_piece(
        downloader,
        0,
        b"X" + pieces[0][1:]
    ) is False

    assert not (download_root / "alphabet.txt").exists()

    assert save_piece(downloader, 0, pieces[0]) is True
    assert get_available_pieces(downloader) == [0]
    assert get_missing_pieces(downloader) == [1, 2]
    assert get_bitfield(downloader) == b"\x80"
    assert get_bytes_left(downloader) == 16

    assert save_piece(downloader, 1, pieces[1]) is True
    assert save_piece(downloader, 2, pieces[2]) is True
    assert get_available_pieces(downloader) == [0, 1, 2]
    assert get_bitfield(downloader) == b"\xe0"
    assert get_bytes_left(downloader) == 0
    assert is_complete(downloader) is True
    assert (
        download_root / "alphabet.txt"
    ).read_bytes() == source_data


print("\n5. Detecting corrupted stored data")

with TemporaryDirectory() as directory:
    corrupt_root = Path(directory) / "downloads"
    corrupt_manager = create_piece_manager(
        info,
        corrupt_root
    )

    for piece_index, piece_data in enumerate(pieces):
        assert save_piece(
            corrupt_manager,
            piece_index,
            piece_data
        ) is True

    corrupt_path = corrupt_root / "alphabet.txt"
    corrupt_data = bytearray(corrupt_path.read_bytes())
    corrupt_data[0] = ord("X")
    corrupt_path.write_bytes(bytes(corrupt_data))

    assert refresh_pieces(corrupt_manager) == [1, 2]
    assert get_available_pieces(corrupt_manager) == [1, 2]
    assert get_missing_pieces(corrupt_manager) == [0]
    assert get_bitfield(corrupt_manager) == b"\x60"
    assert get_bytes_left(corrupt_manager) == 10
    assert is_complete(corrupt_manager) is False


print("\n6. Saving pieces across multiple files")

multi_data = b"ABCDEFGHIJKL"
multi_piece_length = 5
multi_pieces = [
    multi_data[index:index + multi_piece_length]
    for index in range(0, len(multi_data), multi_piece_length)
]

multi_info = {
    b"name": b"collection",
    b"piece length": multi_piece_length,
    b"pieces": b"".join(
        hashlib.sha1(piece_data).digest()
        for piece_data in multi_pieces
    ),
    b"files": [
        {
            b"length": 3,
            b"path": [b"first.txt"]
        },
        {
            b"length": 4,
            b"path": [b"folder", b"second.txt"]
        },
        {
            b"length": 5,
            b"path": [b"third.txt"]
        }
    ]
}

with TemporaryDirectory() as directory:
    multi_root = Path(directory) / "downloads"
    multi_manager = create_piece_manager(
        multi_info,
        multi_root
    )

    for piece_index, piece_data in enumerate(multi_pieces):
        assert save_piece(
            multi_manager,
            piece_index,
            piece_data
        ) is True

    collection = multi_root / "collection"

    assert (collection / "first.txt").read_bytes() == b"ABC"
    assert (
        collection / "folder" / "second.txt"
    ).read_bytes() == b"DEFG"
    assert (collection / "third.txt").read_bytes() == b"HIJKL"
    assert is_complete(multi_manager) is True


print("\n7. Rejecting unsafe torrent paths")

unsafe_info = {
    b"name": b"unsafe",
    b"piece length": 1,
    b"pieces": hashlib.sha1(b"A").digest(),
    b"files": [
        {
            b"length": 1,
            b"path": [b"..", b"outside.txt"]
        }
    ]
}

with TemporaryDirectory() as directory:
    try:
        create_piece_manager(
            unsafe_info,
            directory
        )
        assert False
    except ValueError:
        pass


print("\nAll piece manager tests passed!")
