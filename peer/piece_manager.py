from pathlib import Path
import hashlib
import threading


SHA1_LENGTH = 20


# Read one safe file-name component
def read_path_component(value, field_name):
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError(f"{field_name} must contain valid UTF-8")

    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be bytes or a string")

    if (
        not value
        or value in [".", ".."]
        or "/" in value
        or "\\" in value
        or "\x00" in value
        or Path(value).is_absolute()
    ):
        raise ValueError(f"invalid {field_name}")

    return value


# Read one nonnegative file length
def read_file_length(value):
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("file length must be an integer")

    if value < 0:
        raise ValueError("file length cannot be negative")

    return value


# Build the ordered files for one torrent
def build_file_list(info, data_root):
    has_length = b"length" in info
    has_files = b"files" in info

    if has_length == has_files:
        raise ValueError("info must contain either length or files")

    root = Path(data_root)
    torrent_name = read_path_component(
        info[b"name"],
        "torrent name"
    )

    files = []
    offset = 0

    if has_length:
        length = read_file_length(info[b"length"])

        files.append({
            "path": root / torrent_name,
            "length": length,
            "offset": offset
        })

        return files

    file_entries = info[b"files"]

    if not isinstance(file_entries, list) or not file_entries:
        raise ValueError("files must be a nonempty list")

    used_paths = set()

    for file_entry in file_entries:
        if not isinstance(file_entry, dict):
            raise TypeError("each file entry must be a dictionary")

        if b"length" not in file_entry or b"path" not in file_entry:
            raise ValueError("each file entry needs length and path")

        length = read_file_length(file_entry[b"length"])
        path_values = file_entry[b"path"]

        if not isinstance(path_values, list) or not path_values:
            raise ValueError("file path must be a nonempty list")

        path = root / torrent_name

        for path_value in path_values:
            path = path / read_path_component(
                path_value,
                "file path component"
            )

        if path in used_paths:
            raise ValueError("torrent contains duplicate file paths")

        used_paths.add(path)

        files.append({
            "path": path,
            "length": length,
            "offset": offset
        })

        offset += length

    return files


# Create the state used to manage torrent pieces
def create_piece_manager(info, data_root):
    if not isinstance(info, dict):
        raise TypeError("info must be a dictionary")

    required = [
        b"name",
        b"piece length",
        b"pieces"
    ]

    for field in required:
        if field not in info:
            raise ValueError(f"info has no {field.decode('ascii')} field")

    piece_length = info[b"piece length"]

    if isinstance(piece_length, bool) or not isinstance(piece_length, int):
        raise TypeError("piece length must be an integer")

    if piece_length <= 0:
        raise ValueError("piece length must be positive")

    pieces = info[b"pieces"]

    if not isinstance(pieces, bytes):
        raise TypeError("pieces must be bytes")

    if len(pieces) % SHA1_LENGTH != 0:
        raise ValueError("pieces length must be a multiple of 20")

    files = build_file_list(info, data_root)
    total_size = sum(file_info["length"] for file_info in files)
    expected_piece_count = (
        total_size + piece_length - 1
    ) // piece_length
    piece_count = len(pieces) // SHA1_LENGTH

    if piece_count != expected_piece_count:
        raise ValueError("piece count does not match torrent size")

    piece_hashes = [
        pieces[index:index + SHA1_LENGTH]
        for index in range(0, len(pieces), SHA1_LENGTH)
    ]

    manager = {
        "info": info,
        "data_root": Path(data_root),
        "files": files,
        "total_size": total_size,
        "piece_length": piece_length,
        "piece_hashes": piece_hashes,
        "piece_count": piece_count,
        "have_pieces": set(),
        "lock": threading.Lock()
    }

    refresh_pieces(manager)

    return manager


# Validate one piece index
def validate_piece_index(manager, piece_index):
    if isinstance(piece_index, bool) or not isinstance(piece_index, int):
        raise TypeError("piece index must be an integer")

    if piece_index < 0 or piece_index >= manager["piece_count"]:
        raise IndexError("piece index is out of range")


# Return the number of pieces
def get_piece_count(manager):
    return manager["piece_count"]


# Return one expected piece hash
def get_piece_hash(manager, piece_index):
    validate_piece_index(manager, piece_index)

    return manager["piece_hashes"][piece_index]


# Return the exact size of one piece
def get_piece_size(manager, piece_index):
    validate_piece_index(manager, piece_index)

    start = piece_index * manager["piece_length"]
    remaining = manager["total_size"] - start

    return min(manager["piece_length"], remaining)


# Return the torrent offset of one piece
def get_piece_offset(manager, piece_index):
    validate_piece_index(manager, piece_index)

    return piece_index * manager["piece_length"]


# Find file segments covered by one torrent range
def get_file_segments(manager, offset, length):
    if isinstance(offset, bool) or not isinstance(offset, int):
        raise TypeError("offset must be an integer")

    if isinstance(length, bool) or not isinstance(length, int):
        raise TypeError("length must be an integer")

    if offset < 0 or length < 0:
        raise ValueError("offset and length cannot be negative")

    if offset + length > manager["total_size"]:
        raise ValueError("data range exceeds torrent size")

    end = offset + length
    segments = []

    for file_info in manager["files"]:
        file_start = file_info["offset"]
        file_end = file_start + file_info["length"]
        segment_start = max(offset, file_start)
        segment_end = min(end, file_end)

        if segment_start >= segment_end:
            continue

        segments.append({
            "path": file_info["path"],
            "file_offset": segment_start - file_start,
            "data_offset": segment_start - offset,
            "length": segment_end - segment_start
        })

    covered = sum(segment["length"] for segment in segments)

    if covered != length:
        raise ValueError("torrent files do not cover the data range")

    return segments


# Confirm one torrent file remains inside its data root
def validate_data_path(manager, path):
    root = manager["data_root"].resolve()
    resolved_path = path.resolve()

    if resolved_path != root and root not in resolved_path.parents:
        raise ValueError("torrent file path leaves the data root")


# Read one range from the torrent files
def read_data_range(manager, offset, length):
    segments = get_file_segments(
        manager,
        offset,
        length
    )

    data = bytearray(length)

    for segment in segments:
        validate_data_path(manager, segment["path"])

        with segment["path"].open("rb") as file:
            file.seek(segment["file_offset"])
            block = file.read(segment["length"])

        if len(block) != segment["length"]:
            raise ValueError("torrent file data is incomplete")

        start = segment["data_offset"]
        end = start + segment["length"]
        data[start:end] = block

    return bytes(data)


# Write one range to the torrent files
def write_data_range(manager, offset, data):
    if not isinstance(data, bytes):
        raise TypeError("data must be bytes")

    segments = get_file_segments(
        manager,
        offset,
        len(data)
    )

    for segment in segments:
        path = segment["path"]
        validate_data_path(manager, path)
        path.parent.mkdir(parents=True, exist_ok=True)

        mode = "r+b" if path.exists() else "wb"

        with path.open(mode) as file:
            file.seek(segment["file_offset"])

            start = segment["data_offset"]
            end = start + segment["length"]
            written = file.write(data[start:end])

        if written != segment["length"]:
            raise OSError("could not write complete piece data")


# Check one piece against its expected SHA-1 hash
def verify_piece(manager, piece_index, data):
    validate_piece_index(manager, piece_index)

    if not isinstance(data, bytes):
        raise TypeError("piece data must be bytes")

    if len(data) != get_piece_size(manager, piece_index):
        return False

    actual_hash = hashlib.sha1(data).digest()
    expected_hash = get_piece_hash(manager, piece_index)

    return actual_hash == expected_hash


# Scan the files and record every valid piece
def refresh_pieces(manager):
    valid_pieces = set()

    with manager["lock"]:
        for piece_index in range(manager["piece_count"]):
            try:
                data = read_data_range(
                    manager,
                    get_piece_offset(manager, piece_index),
                    get_piece_size(manager, piece_index)
                )
            except (OSError, ValueError):
                continue

            if verify_piece(manager, piece_index, data):
                valid_pieces.add(piece_index)

        manager["have_pieces"] = valid_pieces

    return sorted(valid_pieces)


# Return all verified piece indexes
def get_available_pieces(manager):
    with manager["lock"]:
        return sorted(manager["have_pieces"])


# Return all missing piece indexes
def get_missing_pieces(manager):
    with manager["lock"]:
        return [
            piece_index
            for piece_index in range(manager["piece_count"])
            if piece_index not in manager["have_pieces"]
        ]


# Check whether one piece is available
def have_piece(manager, piece_index):
    validate_piece_index(manager, piece_index)

    with manager["lock"]:
        return piece_index in manager["have_pieces"]


# Check whether all pieces are available
def is_complete(manager):
    with manager["lock"]:
        return len(manager["have_pieces"]) == manager["piece_count"]


# Return the number of bytes still missing
def get_bytes_left(manager):
    with manager["lock"]:
        return sum(
            get_piece_size(manager, piece_index)
            for piece_index in range(manager["piece_count"])
            if piece_index not in manager["have_pieces"]
        )


# Build the BitTorrent bitfield for available pieces
def get_bitfield(manager):
    bitfield = bytearray(
        (manager["piece_count"] + 7) // 8
    )

    with manager["lock"]:
        for piece_index in manager["have_pieces"]:
            byte_index = piece_index // 8
            bit_index = 7 - piece_index % 8
            bitfield[byte_index] |= 1 << bit_index

    return bytes(bitfield)


# Read one complete verified piece
def read_piece(manager, piece_index):
    validate_piece_index(manager, piece_index)

    with manager["lock"]:
        if piece_index not in manager["have_pieces"]:
            raise ValueError("piece is not available")

        data = read_data_range(
            manager,
            get_piece_offset(manager, piece_index),
            get_piece_size(manager, piece_index)
        )

        if not verify_piece(manager, piece_index, data):
            manager["have_pieces"].discard(piece_index)
            raise ValueError("stored piece failed hash verification")

        return data


# Read one block from a verified piece
def read_piece_block(manager, piece_index, begin, length):
    validate_piece_index(manager, piece_index)

    if isinstance(begin, bool) or not isinstance(begin, int):
        raise TypeError("begin must be an integer")

    if isinstance(length, bool) or not isinstance(length, int):
        raise TypeError("length must be an integer")

    if begin < 0 or length <= 0:
        raise ValueError("begin must be nonnegative and length must be positive")

    if begin + length > get_piece_size(manager, piece_index):
        raise ValueError("requested block exceeds piece size")

    data = read_piece(manager, piece_index)

    return data[begin:begin + length]


# Create and resize every completed torrent file
def finish_files(manager):
    for file_info in manager["files"]:
        path = file_info["path"]
        validate_data_path(manager, path)
        path.parent.mkdir(parents=True, exist_ok=True)

        mode = "r+b" if path.exists() else "wb"

        with path.open(mode) as file:
            file.truncate(file_info["length"])


# Verify and save one complete piece
def save_piece(manager, piece_index, data):
    validate_piece_index(manager, piece_index)

    if not isinstance(data, bytes):
        raise TypeError("piece data must be bytes")

    if not verify_piece(manager, piece_index, data):
        return False

    with manager["lock"]:
        write_data_range(
            manager,
            get_piece_offset(manager, piece_index),
            data
        )

        stored_data = read_data_range(
            manager,
            get_piece_offset(manager, piece_index),
            get_piece_size(manager, piece_index)
        )

        if not verify_piece(manager, piece_index, stored_data):
            manager["have_pieces"].discard(piece_index)
            return False

        manager["have_pieces"].add(piece_index)

        if len(manager["have_pieces"]) == manager["piece_count"]:
            finish_files(manager)

    return True
