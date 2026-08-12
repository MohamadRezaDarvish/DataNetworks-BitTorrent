def bencode(data):
    # bool is a subclass of int in Python, so reject it explicitly
    if isinstance(data, bool):
        raise TypeError("bool is not supported")

    # integer: 42 -> b"i42e"
    if isinstance(data, int):
        return b"i" + str(data).encode("ascii") + b"e"

    # Python string: "hello" -> convert to UTF-8 bytes first
    if isinstance(data, str):
        data = data.encode("utf-8")

    # bytes: b"hello" -> b"5:hello"
    if isinstance(data, bytes):
        return str(len(data)).encode("ascii") + b":" + data

    # list: [b"a", 2] -> b"l1:ai2ee"
    if isinstance(data, list):
        return b"l" + b"".join(bencode(x) for x in data) + b"e"

    # dictionary
    if isinstance(data, dict):
        out = bytearray(b"d")

        for key in sorted(data.keys()):
            if not isinstance(key, bytes):
                raise TypeError("bencode dict keys must be bytes")

            out += bencode(key)
            out += bencode(data[key])

        out += b"e"
        return bytes(out)

    raise TypeError(f"unsupported type: {type(data)!r}")


def bendecode(data):
    if not isinstance(data, bytes):
        raise TypeError("bdecode expects bytes")

    def parse(index):
        if index >= len(data):
            raise ValueError("unexpected end of data")

        # -------------------------
        # INTEGER
        # i42e -> 42
        # -------------------------
        if data[index:index + 1] == b"i":
            end = data.find(b"e", index)

            if end == -1:
                raise ValueError("unterminated integer")

            number_bytes = data[index + 1:end]

            if not number_bytes:
                raise ValueError("empty integer")

            number = int(number_bytes)

            return number, end + 1

        # -------------------------
        # STRING / BYTES
        # 5:hello -> b"hello"
        # -------------------------
        if 48 <= data[index] <= 57:  # ASCII '0'..'9'
            colon = data.find(b":", index)

            if colon == -1:
                raise ValueError("missing ':' in byte string")

            length_bytes = data[index:colon]

            if not length_bytes.isdigit():
                raise ValueError("invalid byte string length")

            length = int(length_bytes)

            start = colon + 1
            end = start + length

            if end > len(data):
                raise ValueError("byte string shorter than declared length")

            value = data[start:end]

            return value, end

        # -------------------------
        # LIST
        # l1:ai2ee -> [b"a", 2]
        # -------------------------
        if data[index:index + 1] == b"l":
            result = []
            index += 1

            while True:
                if index >= len(data):
                    raise ValueError("unterminated list")

                if data[index:index + 1] == b"e":
                    return result, index + 1

                value, index = parse(index)
                result.append(value)

        # -------------------------
        # DICTIONARY
        # d3:foo3:bare
        # -> {b"foo": b"bar"}
        # -------------------------
        if data[index:index + 1] == b"d":
            result = {}
            index += 1

            while True:
                if index >= len(data):
                    raise ValueError("unterminated dictionary")

                if data[index:index + 1] == b"e":
                    return result, index + 1

                key, index = parse(index)

                if not isinstance(key, bytes):
                    raise TypeError("dictionary key must be bytes")

                value, index = parse(index)

                result[key] = value

        raise ValueError(
            f"invalid bencode value at position {index}"
        )

    result, next_index = parse(0)

    # There should be nothing after the first complete object
    if next_index != len(data):
        raise ValueError("extra data after bencoded object")

    return result