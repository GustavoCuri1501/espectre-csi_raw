/*
 * ESPectre - Raw CSI Streamer Header
 * 
 * Streams raw CSI data (all 64 subcarriers) via UDP in binary format.
 * Optimized for minimal network traffic while preserving full CSI information.
 * 
 * Packet format (140 bytes total):
 *   Header (12 bytes):
 *     - Magic: 0x43535241 ("CSRA" = CSI Raw)
 *     - Timestamp: uint32 (milliseconds since boot)
 *     - Sequence: uint16 (packet counter)
 *     - Channel: uint8 (WiFi channel)
 *     - Flags: uint8 (bit 0 = gain_locked, bit 1 = stbc)
 *   Payload (128 bytes):
 *     - I/Q values: int8[128] (64 subcarriers × 2, interleaved)
 * 
 * Author: Francesco Pace <francesco.pace@gmail.com>
 * License: GPLv3
 */

#pragma once

#include <cstdint>
#include <string>
#include <cstring>
#include "esphome/core/component.h"
#include "esphome/core/hal.h"
#include "esp_wifi.h"
#include <lwip/sockets.h>
#include <lwip/netdb.h>
#include <netinet/in.h>

namespace esphome {
namespace espectre {

// Binary packet constants
constexpr uint32_t RAW_CSI_MAGIC = 0x43535241;  // "CSRA" in little-endian
constexpr uint8_t RAW_CSI_HEADER_SIZE = 12;
constexpr uint8_t RAW_CSI_PAYLOAD_SIZE = 128;   // 64 subcarriers × 2 bytes
constexpr uint8_t RAW_CSI_PACKET_SIZE = RAW_CSI_HEADER_SIZE + RAW_CSI_PAYLOAD_SIZE;

// Flag bits
constexpr uint8_t RAW_CSI_FLAG_GAIN_LOCKED = 0x01;
constexpr uint8_t RAW_CSI_FLAG_STBC = 0x02;

class RawCSIStreamer : public Component {
 public:
  void setup() override;
  void loop() override;
  float get_setup_priority() const override { return setup_priority::AFTER_WIFI; }
  
  /**
   * Initialize the streamer
   * 
   * @param server_ip Destination server IP address
   * @param server_port Destination server UDP port
   * @param interval_ms Send interval in milliseconds (2-1000ms, default 10ms = 100Hz)
   */
  void init(const std::string &server_ip, uint16_t server_port, uint32_t interval_ms = 10);
  
  /**
   * Start streaming CSI data
   */
  void start();
  
  /**
   * Stop streaming CSI data
   */
  void stop();
  
  /**
   * Check if streaming is active
   */
  bool is_running() const { return running_; }
  
  /**
   * Send a raw CSI packet
   * 
   * @param csi_data Raw I/Q data (int8 array, 128 bytes for 64 subcarriers)
   * @param csi_len Length of CSI data (should be 128)
   * @param timestamp Timestamp in milliseconds
   * @param channel WiFi channel
   * @param gain_locked Whether gain is locked
   */
  void send_packet(const int8_t* csi_data, size_t csi_len, uint32_t timestamp,
                   uint8_t channel = 0, bool gain_locked = true);
  
  /**
   * Set server destination
   * 
   * @param server_ip New server IP address
   * @param server_port New server UDP port
   */
  void set_server(const std::string &server_ip, uint16_t server_port);
  
  /**
   * Set send interval
   * 
   * @param interval_ms New interval in milliseconds
   */
  void set_interval(uint32_t interval_ms);
  
  /**
   * Get statistics
   */
  uint32_t get_packets_sent() const { return packets_sent_; }
  uint32_t get_packets_dropped() const { return packets_dropped_; }

 protected:
  /**
   * Build and send a binary packet
   */
  bool send_binary_packet_(const int8_t* csi_data, size_t csi_len, uint32_t timestamp,
                           uint8_t channel, uint8_t flags);
  
  /**
   * Initialize WiFi socket
   */
  bool init_socket_();
  
  /**
   * Check if we should send next packet
   */
  bool should_send_() const;

  // Configuration
  std::string server_ip_;
  uint16_t server_port_{5001};
  uint32_t interval_ms_{10};  // 10ms = 100Hz default
  
  // State
  bool running_{false};
  bool socket_initialized_{false};
  
  // Statistics
  uint32_t packets_sent_{0};
  uint32_t packets_dropped_{0};
  
  // Timing
  uint32_t last_send_time_ms_{0};
  uint32_t next_send_time_ms_{0};
  
  // Sequence counter
  uint16_t sequence_{0};
  
  // Buffer for packet building
  uint8_t packet_buffer_[RAW_CSI_PACKET_SIZE];
  
  // WiFi address
  sockaddr_in server_addr_;
};

}  // namespace espectre
}  // namespace esphome
