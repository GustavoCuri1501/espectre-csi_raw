#!/usr/bin/env python3
"""
CSI Receiver Server - Receives raw CSI data from ESP32-S3 LilyGO

Receives UDP packets with raw CSI data (64 subcarriers) and:
- Validates packet format
- Calculates basic statistics
- Detects motion using variance threshold
- Saves data to CSV/binary files

Author: Developed for ESPectre Raw CSI Streaming
License: GPLv3
"""

import socket
import struct
import sys
import time
import argparse
import numpy as np
from pathlib import Path
from datetime import datetime
from collections import deque
from typing import Optional, Tuple, List

# Packet format constants
MAGIC = 0x43535241  # "CSRA" in little-endian
HEADER_FORMAT = "<IIBB"  # magic(4) + timestamp(4) + seq(2) + channel(1) + flags(1)
HEADER_SIZE = 12
CSI_SIZE = 128  # 64 subcarriers × 2 (I/Q)
PACKET_SIZE = HEADER_SIZE + CSI_SIZE

# Flag bits
FLAG_GAIN_LOCKED = 0x01
FLAG_STBC = 0x02


class CSIPacket:
    """Represents a parsed CSI packet"""

    def __init__(
        self,
        timestamp: int,
        seq: int,
        channel: int,
        gain_locked: bool,
        stbc: bool,
        csi_data: np.ndarray,
    ):
        self.timestamp = timestamp
        self.seq = seq
        self.channel = channel
        self.gain_locked = gain_locked
        self.stbc = stbc
        self.csi_data = csi_data  # int8[128]

        # Derived properties
        self.amplitudes = np.abs(
            self.csi_data[1::2].astype(np.float32)
            + 1j * self.csi_data[0::2].astype(np.float32)
        )
        self.phases = np.angle(
            self.csi_data[1::2].astype(np.float32)
            + 1j * self.csi_data[0::2].astype(np.float32)
        )


class CSIMotionDetector:
    """Simple motion detector using CSI variance"""

    def __init__(self, window_size: int = 75, threshold: float = 1.0):
        self.window_size = window_size
        self.threshold = threshold
        self.turbulence_buffer = deque(maxlen=window_size)
        self.baseline_mean = None
        self.baseline_std = None
        self.initialized = False

    def calculate_turbulence(self, packet: CSIPacket) -> float:
        """Calculate spatial turbulence (std of amplitudes)"""
        return float(np.std(packet.amplitudes))

    def add_sample(self, turbulence: float):
        """Add turbulence sample to buffer"""
        self.turbulence_buffer.append(turbulence)

        # Initialize baseline after collecting enough samples
        if not self.initialized and len(self.turbulence_buffer) >= self.window_size:
            self.baseline_mean = np.mean(self.turbulence_buffer)
            self.baseline_std = np.std(self.turbulence_buffer)
            self.initialized = True
            print(
                f"[CALIBRATION] Baseline: mean={self.baseline_mean:.4f}, "
                f"std={self.baseline_std:.4f}"
            )

    def detect_motion(self, packet: CSIPacket) -> Tuple[bool, float]:
        """
        Detect motion based on turbulence variance

        Returns: (is_motion, turbulence_value)
        """
        turbulence = self.calculate_turbulence(packet)
        self.add_sample(turbulence)

        if not self.initialized or len(self.turbulence_buffer) < self.window_size:
            return False, turbulence

        # Calculate variance of recent turbulence
        buffer_array = np.array(self.turbulence_buffer)
        variance = float(np.var(buffer_array))

        # Simple threshold-based detection
        is_motion = variance > self.threshold

        return is_motion, turbulence


class CSIServer:
    """UDP server for receiving CSI data from ESP32"""

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 5001,
        save_dir: str = "csi_data",
        save_format: str = "both",
        enable_motion_detection: bool = True,
    ):
        self.host = host
        self.port = port
        self.save_dir = Path(save_dir)
        self.save_format = save_format  # 'csv', 'binary', or 'both'
        self.enable_motion_detection = enable_motion_detection

        # Statistics
        self.packets_received = 0
        self.packets_invalid = 0
        self.packets_dropped = 0
        self.last_seq = -1
        self.start_time = None
        self.packets_per_second = 0.0

        # Motion detection
        self.motion_detector = None
        if enable_motion_detection:
            self.motion_detector = CSIMotionDetector(window_size=75, threshold=1.0)

        # File handles
        self.csv_file = None
        self.bin_file = None

        # Buffer for recent packets
        self.recent_packets = deque(maxlen=1000)

    def setup_files(self):
        """Create output directory and files"""
        self.save_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        if self.save_format in ["csv", "both"]:
            csv_path = self.save_dir / f"csi_data_{timestamp}.csv"
            self.csv_file = open(csv_path, "w")
            # Write CSV header
            header = "timestamp,seq,channel,gain_locked,stbc,"
            header += ",".join([f"ampl_{i}" for i in range(64)]) + ","
            header += ",".join([f"phase_{i}" for i in range(64)]) + "\n"
            self.csv_file.write(header)
            print(f"[CSV] Saving to: {csv_path}")

        if self.save_format in ["binary", "both"]:
            bin_path = self.save_dir / f"csi_data_{timestamp}.bin"
            self.bin_file = open(bin_path, "wb")
            print(f"[BINARY] Saving to: {bin_path}")

    def close_files(self):
        """Close file handles"""
        if self.csv_file:
            self.csv_file.close()
        if self.bin_file:
            self.bin_file.close()

    def save_packet(self, packet: CSIPacket, is_motion: bool = False):
        """Save packet to file(s)"""
        if self.save_format in ["csv", "both"] and self.csv_file:
            # CSV format
            amplitudes = ",".join([f"{a:.4f}" for a in packet.amplitudes])
            phases = ",".join([f"{p:.6f}" for p in packet.phases])
            line = f"{packet.timestamp},{packet.seq},{packet.channel},"
            line += (
                f"{int(packet.gain_locked)},{int(packet.stbc)},{amplitudes},{phases}\n"
            )
            self.csv_file.write(line)
            self.csv_file.flush()

        if self.save_format in ["binary", "both"] and self.bin_file:
            # Binary format (same as received)
            header = struct.pack(
                HEADER_FORMAT,
                MAGIC,
                packet.timestamp,
                packet.seq,
                packet.channel,
                (FLAG_GAIN_LOCKED if packet.gain_locked else 0)
                | (FLAG_STBC if packet.stbc else 0),
            )
            self.bin_file.write(header)
            self.bin_file.write(packet.csi_data.tobytes())
            self.bin_file.flush()

    def parse_packet(self, data: bytes) -> Optional[CSIPacket]:
        """Parse raw UDP data into CSIPacket"""
        if len(data) != PACKET_SIZE:
            self.packets_invalid += 1
            if self.packets_invalid % 100 == 1:
                print(
                    f"[WARN] Invalid packet size: {len(data)} (expected {PACKET_SIZE})"
                )
            return None

        # Parse header
        try:
            magic, timestamp, seq, channel, flags = struct.unpack(
                HEADER_FORMAT, data[:HEADER_SIZE]
            )
        except struct.error as e:
            self.packets_invalid += 1
            print(f"[ERROR] Failed to parse header: {e}")
            return None

        # Validate magic
        if magic != MAGIC:
            self.packets_invalid += 1
            if self.packets_invalid % 100 == 1:
                print(f"[WARN] Invalid magic: 0x{magic:08X} (expected 0x{MAGIC:08X})")
            return None

        # Parse CSI data
        csi_data = np.frombuffer(data[HEADER_SIZE:], dtype=np.int8)

        # Extract flags
        gain_locked = bool(flags & FLAG_GAIN_LOCKED)
        stbc = bool(flags & FLAG_STBC)

        # Detect dropped packets
        if self.last_seq >= 0:
            expected = (self.last_seq + 1) % 65536
            if seq != expected:
                dropped = (seq - expected) % 65536
                if dropped > 0:
                    self.packets_dropped += dropped

        self.last_seq = seq

        return CSIPacket(timestamp, seq, channel, gain_locked, stbc, csi_data)

    def print_status(self):
        """Print current status"""
        if self.start_time:
            elapsed = time.time() - self.start_time
            self.packets_per_second = self.packets_received / elapsed

            print(
                f"\r[STATS] Received: {self.packets_received:6d} | "
                f"Invalid: {self.packets_invalid:4d} | "
                f"Dropped: {self.packets_dropped:6d} | "
                f"PPS: {self.packets_per_second:6.1f} | "
                f"Duration: {elapsed:6.1f}s",
                end="",
                flush=True,
            )

    def run(self):
        """Main server loop"""
        print("=" * 60)
        print("  ESPectre CSI Receiver Server")
        print("=" * 60)
        print(f"  Listening on: {self.host}:{self.port}")
        print(f"  Packet size: {PACKET_SIZE} bytes")
        print(f"  Expected rate: ~100 Hz (14 KB/s)")
        print("=" * 60)
        print()

        # Setup
        self.setup_files()

        # Create UDP socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        try:
            sock.bind((self.host, self.port))
            sock.settimeout(1.0)  # 1 second timeout for graceful updates
        except OSError as e:
            print(f"[ERROR] Failed to bind socket: {e}")
            print(f"  Make sure port {self.port} is not in use")
            return

        print(f"[READY] Server started. Waiting for CSI data...")
        print(f"[INFO] Press Ctrl+C to stop\n")

        self.start_time = time.time()

        try:
            while True:
                try:
                    # Receive packet
                    data, addr = sock.recvfrom(PACKET_SIZE + 100)  # Extra buffer

                    # Parse packet
                    packet = self.parse_packet(data)
                    if packet is None:
                        continue

                    self.packets_received += 1
                    self.recent_packets.append(packet)

                    # Motion detection
                    is_motion = False
                    if self.motion_detector:
                        is_motion, turbulence = self.motion_detector.detect_motion(
                            packet
                        )
                        if is_motion and self.packets_received % 10 == 1:
                            print(f"\n[MOTION] Detected! Turbulence: {turbulence:.4f}")

                    # Save packet
                    self.save_packet(packet, is_motion)

                    # Update status every 100 packets
                    if self.packets_received % 100 == 0:
                        self.print_status()

                except socket.timeout:
                    # Periodic status update
                    if self.packets_received > 0:
                        self.print_status()
                    continue

        except KeyboardInterrupt:
            print("\n\n[STOP] Server stopped by user")

        finally:
            # Final statistics
            print("\n" + "=" * 60)
            print("  Final Statistics")
            print("=" * 60)

            elapsed = time.time() - self.start_time
            print(f"  Duration:           {elapsed:.1f} seconds")
            print(f"  Packets received:   {self.packets_received}")
            print(f"  Packets invalid:    {self.packets_invalid}")
            print(f"  Packets dropped:    {self.packets_dropped}")
            print(
                f"  Average PPS:        {self.packets_received / max(elapsed, 1):.1f}"
            )
            print(
                f"  Average bitrate:    {self.packets_received * PACKET_SIZE / max(elapsed, 1) / 1024:.1f} KB/s"
            )

            if self.packets_received > 0:
                drop_rate = self.packets_dropped / self.packets_received * 100
                print(f"  Drop rate:          {drop_rate:.2f}%")

            print("=" * 60)

            self.close_files()
            sock.close()


def main():
    parser = argparse.ArgumentParser(
        description="ESPectre CSI Receiver Server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python csi_receiver.py                    # Default: 0.0.0.0:5001
  python csi_receiver.py -p 5002            # Custom port
  python csi_receiver.py --save-csv         # Save only CSV
  python csi_receiver.py --no-motion        # Disable motion detection
        """,
    )

    parser.add_argument(
        "-H", "--host", default="0.0.0.0", help="Host IP to bind (default: 0.0.0.0)"
    )
    parser.add_argument(
        "-p",
        "--port",
        type=int,
        default=5001,
        help="UDP port to listen (default: 5001)",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="csi_data",
        help="Output directory (default: csi_data)",
    )
    parser.add_argument("--save-csv", action="store_true", help="Save only CSV format")
    parser.add_argument(
        "--save-binary", action="store_true", help="Save only binary format"
    )
    parser.add_argument(
        "--no-motion", action="store_true", help="Disable motion detection"
    )
    parser.add_argument(
        "--window",
        type=int,
        default=75,
        help="Motion detection window size (default: 75)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=1.0,
        help="Motion detection threshold (default: 1.0)",
    )

    args = parser.parse_args()

    # Determine save format
    save_format = "both"
    if args.save_csv:
        save_format = "csv"
    elif args.save_binary:
        save_format = "binary"

    # Create and run server
    server = CSIServer(
        host=args.host,
        port=args.port,
        save_dir=args.output,
        save_format=save_format,
        enable_motion_detection=not args.no_motion,
    )

    if args.no_motion:
        server.motion_detector = None

    server.run()


if __name__ == "__main__":
    main()
