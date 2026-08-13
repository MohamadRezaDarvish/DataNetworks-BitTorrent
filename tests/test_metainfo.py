from pathlib import Path
from tempfile import TemporaryDirectory
import hashlib

from common.bencode import bencode
from common.metainfo import (
    calculate_info_hash,
    get_total_size,
    get_tracker_url,
    load_torrent
)


TORRENT = Path("metainfo/alphabet.torrent")
SOURCE = Path("shared_files/alphabet.txt")
TRACKER_URL = "http://127.0.0.1:6969/announce"


# Check that one call raises an expected error
def expect_error(function, error_type):
    try:
        function()
        assert False
    except error_type:
        pass


print("1. Loading the alphabet torrent")

metainfo = load_torrent(TORRENT)

assert isinstance(metainfo, dict)
assert b"announce" in metainfo
assert b"info" in metainfo

info = metainfo[b"info"]


print("\n2. Calculating the info hash")

info_hash = calculate_info_hash(info)
expected_hash = hashlib.sha1(
    bencode(info)
).digest()

assert isinstance(info_hash, bytes)
assert len(info_hash) == 20
assert info_hash == expected_hash


print("\n3. Reading single-file information")

assert get_total_size(info) == SOURCE.stat().st_size
assert get_tracker_url(metainfo) == TRACKER_URL


print("\n4. Reading multi-file information")

multi_info = {
    b"name": b"collection",
    b"piece length": 5,
    b"pieces": b"",
    b"files": [
        {
            b"length": 3,
            b"path": [b"first.txt"]
        },
        {
            b"length": 4,
            b"path": [b"second.txt"]
        },
        {
            b"length": 5,
            b"path": [b"third.txt"]
        }
    ]
}

assert get_total_size(multi_info) == 12


print("\n5. Reading tracker URL forms")

assert get_tracker_url(
    {b"announce": TRACKER_URL.encode("utf-8")}
) == TRACKER_URL

assert get_tracker_url(
    {
        b"announce": [
            TRACKER_URL.encode("utf-8"),
            b"http://127.0.0.1:6970/announce"
        ]
    }
) == TRACKER_URL


print("\n6. Rejecting invalid torrent files")

with TemporaryDirectory(dir=".") as directory:
    root = Path(directory)

    not_dictionary = root / "not_dictionary.torrent"
    missing_announce = root / "missing_announce.torrent"
    missing_info = root / "missing_info.torrent"

    not_dictionary.write_bytes(
        bencode([b"invalid"])
    )

    missing_announce.write_bytes(
        bencode({b"info": info})
    )

    missing_info.write_bytes(
        bencode({b"announce": TRACKER_URL.encode("utf-8")})
    )

    expect_error(
        lambda: load_torrent(not_dictionary),
        TypeError
    )

    expect_error(
        lambda: load_torrent(missing_announce),
        ValueError
    )

    expect_error(
        lambda: load_torrent(missing_info),
        ValueError
    )


print("\n7. Rejecting invalid Metainfo values")

expect_error(
    lambda: get_total_size({}),
    ValueError
)

expect_error(
    lambda: get_tracker_url({b"announce": []}),
    ValueError
)

expect_error(
    lambda: get_tracker_url({b"announce": 6969}),
    ValueError
)


print("\nAll Metainfo tests passed!")
