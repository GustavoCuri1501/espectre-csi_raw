#!/usr/bin/env python3
"""
CSI Visualizer - Real-time visualization of CSI data

Visualizes:
- Amplitude of all 64 subcarriers
- Motion detection status
- Packet statistics

Requirements: matplotlib, numpy
"""

import matplotlib.pyplot as plt
import matplotlib.animation as animation
from matplotlib.gridspec import GridSpec
import numpy as np
import socket
import struct
import argparse
from collections import deque

# Constants
MAGIC = 0x43535241
HEADER_FORMAT = "<IIBB"
HEADER_SIZE = 12
CSI_SIZE = 128
PACKET_SIZE = 140


class CSIVisualizer:
    def __init__(self, host="0.0.0.0", port=5001):
        self.host = host
        self.port = port

        # Data buffers
        self.amplitudes_history = deque(maxlen=100)
        self.turbulence_history = deque(maxlen=100)
        self.packets_received = 0
        self.last_turbulence = 0.0
        self.is_motion = False

        # Setup plot
        self.setup_plot()

        # Setup socket
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(0.1)

    def setup_plot(self):
        """Setup matplotlib figure"""
        self.fig = plt.figure(figsize=(14, 8))
        gs = GridSpec(3, 1, height_ratios=[2, 1, 1])

        # Subcarrier amplitudes
        self.ax_amplitudes = self.fig.add_subplot(gs[0])
        self.ax_amplitudes.set_title("CSI Subcarrier Amplitudes (64 subcarriers)")
        self.ax_amplitudes.set_xlabel("Subcarrier Index")
        self.ax_amplitudes.set_ylabel("Amplitude")
        self.ax_amplitudes.set_xlim(0, 63)
        self.ax_amplitudes.set_ylim(0, 50)
        self.ax_amplitudes.grid(True, alpha=0.3)

        # Line for current amplitudes
        (self.ampl_line,) = self.ax_amplitudes.plot(
            range(64), np.zeros(64), "b-", linewidth=1.5, label="Current"
        )
        self.ax_amplitudes.legend(loc="upper right")

        # Turbulence (motion metric)
        self.ax_turbulence = self.fig.add_subplot(gs[1])
        self.ax_turbulence.set_title("Spatial Turbulence (Motion Detection)")
        self.ax_turbulence.set_xlabel("Time")
        self.ax_turbulence.set_ylabel("Turbulence")
        self.ax_turbulence.set_xlim(0, 100)
        self.ax_turbulence.set_ylim(0, 20)
        self.ax_turbulence.grid(True, alpha=0.3)

        # Turbulence line
        (self.turb_line,) = self.ax_turbulence.plot([], [], "r-", linewidth=1)

        # Motion threshold line
        self.threshold = 1.0
        self.ax_turbulence.axhline(
            y=self.threshold, color="orange", linestyle="--", label="Threshold"
        )
        self.ax_turbulence.legend(loc="upper right")

        # Statistics
        self.ax_stats = self.fig.add_subplot(gs[2])
        self.ax_stats.axis("off")
        self.stats_text = self.ax_stats.text(
            0.1,
            0.5,
            "",
            fontsize=12,
            verticalalignment="center",
            fontfamily="monospace",
        )

        plt.tight_layout()

    def receive_packet(self):
        """Receive and parse one packet"""
        try:
            data, addr = self.sock.recvfrom(PACKET_SIZE + 100)

            if len(data) != PACKET_SIZE:
                return None

            # Parse header
            magic, timestamp, seq, channel, flags = struct.unpack(
                HEADER_FORMAT, data[:HEADER_SIZE]
            )

            if magic != MAGIC:
                return None

            # Parse CSI data
            csi_data = np.frombuffer(data[HEADER_SIZE:], dtype=np.int8)

            # Calculate amplitudes (I/Q interleaved)
            I = csi_data[1::2].astype(np.float32)
            Q = csi_data[0::2].astype(np.float32)
            amplitudes = np.sqrt(I**2 + Q**2)

            # Calculate turbulence (std of amplitudes)
            turbulence = float(np.std(amplitudes))

            # Detect motion
            is_motion = turbulence > self.threshold

            self.packets_received += 1
            self.amplitudes_history.append(amplitudes)
            self.turbulence_history.append(turbulence)
            self.last_turbulence = turbulence
            self.is_motion = is_motion

            return amplitudes, turbulence, is_motion

        except socket.timeout:
            return None
        except Exception as e:
            print(f"Error receiving packet: {e}")
            return None

    def update(self, frame):
        """Update plot (called by animation)"""
        # Receive packets (may receive multiple per frame)
        for _ in range(10):  # Try to receive up to 10 packets per frame
            result = self.receive_packet()
            if result is None:
                break

        if len(self.amplitudes_history) > 0:
            # Update amplitudes plot
            current_ampl = self.amplitudes_history[-1]
            self.ampl_line.set_ydata(current_ampl)

            # Update turbulence plot
            turb_array = np.array(self.turbulence_history)
            self.turb_line.set_data(range(len(turb_array)), turb_array)
            self.ax_turbulence.set_xlim(max(0, len(turb_array) - 100), len(turb_array))

            # Update statistics
            stats = f"""
Packets Received: {self.packets_received}
Current Turbulence: {self.last_turbulence:.4f}
Motion: {"YES ⚡" if self.is_motion else "No"}
Average PPS: {self.packets_received / max(1, (frame + 1) / 60):.1f}
            """
            self.stats_text.set_text(stats)

            # Change background color on motion
            if self.is_motion:
                self.ax_turbulence.set_facecolor("#ffcccc")
            else:
                self.ax_turbulence.set_facecolor("white")

        return self.ampl_line, self.turb_line, self.stats_text

    def run(self):
        """Main visualization loop"""
        print("=" * 60)
        print("  ESPectre CSI Visualizer")
        print("=" * 60)
        print(f"  Listening on: {self.host}:{self.port}")
        print("=" * 60)
        print()
        print("Waiting for CSI data...")
        print("Press Ctrl+C to stop\n")

        try:
            self.sock.bind((self.host, self.port))

            # Start animation
            self.ani = animation.FuncAnimation(
                self.fig, self.update, interval=50, blit=False, cache_frame_data=False
            )

            plt.show()

        except KeyboardInterrupt:
            print("\n[STOP] Visualizer stopped")
        finally:
            self.sock.close()


def main():
    parser = argparse.ArgumentParser(description="ESPectre CSI Visualizer")
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

    args = parser.parse_args()

    visualizer = CSIVisualizer(host=args.host, port=args.port)
    visualizer.run()


if __name__ == "__main__":
    main()
