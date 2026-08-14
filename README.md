# BitTorrent-Style P2P File Distribution

This project implements a BitTorrent-style file distribution system in Python. A multithreaded HTTP tracker coordinates peer discovery, while peers exchange verified file pieces directly over TCP.

The runtime uses only the Python standard library. Wireshark is used to observe network traffic.

## Environment and project location

The project was developed and executed in an Ubuntu virtual machine from:

```text
/home/mohammadreza/DataNetworks/Project
```

The same directory can be reached with:

```bash
cd ~/DataNetworks/Project
```

Create and activate a virtual environment if needed:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

No package installation is required for the tracker or peers.

## Project structure
```text
Project
├── common
│   ├── bencode.py
│   ├── logger.py
│   ├── metainfo.py
│   ├── protocol.py
├── downloads
│   ├── alphabet_peer_6887
│   │   └── alphabet.txt
│   ├── time_swarm_peer2
│   │   └── time_machine.txt
│   ├── time_swarm_peer3
│   │   └── time_machine.txt
│   └── wireshark_run
│       └── network.jpg
├── logs
│   ├── peer_6881.log
│   ├── peer_6882.log
│   ├── peer_6883.log
│   ├── peer_6884.log
│   ├── peer_6885.log
│   ├── peer_6886.log
│   ├── peer_6887.log
│   └── tracker.log
├── metainfo
│   ├── alphabet.torrent
│   ├── network.torrent
│   └── time_machine.torrent
├── peer
│   ├── messages.py
│   ├── peer.py
│   └── piece_manager.py
├── README.md
├── shared_files
│   ├── alphabet.txt
│   ├── network.jpg
│   └── time_machine.txt
├── torrent_generator.py
├── tracker
│   └── tracker.py
└── wireshark
    ├── final_capture2.pcapng
    ├── final_capture3.pcapng
    ├── final_capture.pcapng
    ├── Follow_TCP_Stream
    └── Follow_TCP_Stream_Hex_Dump
```


- `common/bencode.py` - encodes and decodes bencoded values.
- `common/logger.py` - writes tracker and peer event logs.
- `common/metainfo.py` - loads metainfo and provides torrent identity, size, and tracker URL helpers.
- `common/protocol.py` - contains handshake sizes and peer-wire message IDs.
- `tracker/tracker.py` - implements the multithreaded HTTP tracker and swarm state.
- `peer/messages.py` - builds and parses handshakes and peer-wire messages.
- `peer/piece_manager.py` - scans, reads, verifies, saves, and maps torrent pieces to files.
- `peer/peer.py` - coordinates tracker announces, TCP connections, downloads, uploads, pings, and torrent sessions.
- `torrent_generator.py` - generates one `.torrent` file for every ordinary file in `shared_files/`.
- `metainfo/` - contains the generated torrent files.
- `shared_files/` - contains original files used by seeders.
- `downloads/` - contains data downloaded by peers.
- `logs/` - contains `tracker.log` and one log per peer port.
- `wireshark/` - contains saved packet captures and Follow TCP Stream evidence (`final_capture.pcapng` is for `network.jpg`, `final_capture2.pcapng` is for `time_machine.txt`, and `final_capture3.pcapng` is for `alphabet.txt`).
- `tests/` - contains development checks when this folder is included.

## Generate metainfo

Run this after adding or changing files in `shared_files/`:

```bash
PYTHONPATH=. python torrent_generator.py
```

This regenerates the matching files in `metainfo/`.

The supplied metainfo uses `127.0.0.1` for the tracker. For a multi-VM run, the tracker address in the metainfo must be changed or regenerated using an IP address reachable from the peer VMs.

## Run an end-to-end transfer

Use separate terminals and run every command from the project root.

### Terminal 1: tracker

```bash
source .venv/bin/activate
PYTHONPATH=. python -m tracker.tracker
```

### Terminal 2: seeder on port 6881

```bash
source .venv/bin/activate
PYTHONPATH=. python -c 'from peer.peer import run_peer; run_peer("metainfo/network.torrent", 6881, "shared_files")'
```

### Terminal 3: downloader on port 6882

```bash
source .venv/bin/activate
mkdir -p downloads/readme_demo
PYTHONPATH=. python -c 'from peer.peer import run_peer; run_peer("metainfo/network.torrent", 6882, "downloads/readme_demo")'
```

Stop a peer with `Ctrl+C`. It will announce `stopped` and close its connections.

## Verify the result

After the downloader reports completion:

```bash
cmp shared_files/network.jpg downloads/readme_demo/network.jpg
```

No output means the files are byte-for-byte identical.

Useful logs:

```bash
tail -f logs/tracker.log
tail -f logs/peer_6882.log
```

For Wireshark, capture the `lo` interface with:

```text
tcp port 6969 or tcp portrange 6881-6889
```


## Default network settings

- Tracker service: `127.0.0.1:6969`
- Peer listeners: normally `6881-6889`
- Tracker announce interval: 30 seconds
- Stale-peer timeout: 90 seconds
- Peer keep-alive interval: 15 seconds
- Maximum requested block: 16 KiB

Use a separate listening port and download directory for each peer. A seeder's data root must contain the original file named by the selected torrent.
