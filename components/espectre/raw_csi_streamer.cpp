/*
 * ESPectre - Raw CSI Streamer Implementation
 * 
 * Streams raw CSI data (all 64 subcarriers) via UDP in binary format.
 * Uses non-blocking socket operations for minimal latency.
 * 
 * Author: Francesco Pace <francesco.pace@gmail.com>
 * License: GPLv3
 */

#include "raw_csi_streamer.h"
#include "esphome/core/log.h"
#include "esphome/core/application.h"
#include <lwip/sockets.h>
#include <lwip/netdb.h>
#include <cstring>
#include <sys/socket.h>
#include <netinet/in.h>

namespace esphome {
namespace espectre {

static const char *const TAG = "raw_csi_streamer";

void RawCSIStreamer::setup() {
  // Setup is handled by init() called from main component
  ESP_LOGD(TAG, "Raw CSI Streamer setup complete");
}

void RawCSIStreamer::loop() {
  // Non-blocking loop - check if we should send
  // Actual sending happens via callback from CSI manager, so this is just a placeholder
  if (!running_) return;
}

void RawCSIStreamer::init(const std::string &server_ip, uint16_t server_port, uint32_t interval_ms) {
  server_ip_ = server_ip;
  server_port_ = server_port;
  interval_ms_ = interval_ms;
  
  // Validate interval
  if (interval_ms_ < 2) {
    interval_ms_ = 2;  // Minimum 500Hz
    ESP_LOGW(TAG, "Interval clamped to minimum 2ms (500Hz)");
  } else if (interval_ms_ > 1000) {
    interval_ms_ = 1000;  // Maximum 1Hz
    ESP_LOGW(TAG, "Interval clamped to maximum 1000ms (1Hz)");
  }
  
  // Initialize socket
  if (!init_socket_()) {
    ESP_LOGE(TAG, "Failed to initialize socket");
    return;
  }
  
  ESP_LOGI(TAG, "Raw CSI Streamer initialized");
  ESP_LOGI(TAG, "  Server: %s:%u", server_ip_.c_str(), server_port_);
  ESP_LOGI(TAG, "  Interval: %ums (%.1f Hz)", interval_ms_, 1000.0f / interval_ms_);
  ESP_LOGI(TAG, "  Packet size: %u bytes", RAW_CSI_PACKET_SIZE);
}

bool RawCSIStreamer::init_socket_() {
  // Parse server IP
  struct hostent *server = gethostbyname(server_ip_.c_str());
  if (server == nullptr) {
    ESP_LOGE(TAG, "Failed to resolve server IP: %s", server_ip_.c_str());
    return false;
  }
  
  // Setup server address
  memset(&server_addr_, 0, sizeof(server_addr_));
  server_addr_.sin_family = AF_INET;
  server_addr_.sin_port = htons(server_port_);
  memcpy(&server_addr_.sin_addr.s_addr, server->h_addr_list[0], server->h_length);
  
  socket_initialized_ = true;
  ESP_LOGD(TAG, "Socket initialized successfully");
  
  return true;
}

void RawCSIStreamer::start() {
  if (running_) {
    ESP_LOGW(TAG, "Streamer already running");
    return;
  }
  
  if (!socket_initialized_) {
    ESP_LOGE(TAG, "Socket not initialized, calling init_socket_()");
    if (!init_socket_()) {
      return;
    }
  }
  
  running_ = true;
  packets_sent_ = 0;
  packets_dropped_ = 0;
  sequence_ = 0;
  last_send_time_ms_ = 0;
  next_send_time_ms_ = 0;
  
  ESP_LOGI(TAG, "Raw CSI streaming started");
}

void RawCSIStreamer::stop() {
  if (!running_) return;
  
  running_ = false;
  
  ESP_LOGI(TAG, "Raw CSI streaming stopped");
  ESP_LOGI(TAG, "Total packets sent: %u", packets_sent_);
  ESP_LOGI(TAG, "Total packets dropped: %u", packets_dropped_);
}

void RawCSIStreamer::send_packet(const int8_t* csi_data, size_t csi_len, uint32_t timestamp,
                                 uint8_t channel, bool gain_locked) {
  if (!running_) return;
  
  if (csi_data == nullptr || csi_len != RAW_CSI_PAYLOAD_SIZE) {
    ESP_LOGW(TAG, "Invalid CSI data: len=%zu (expected %u)", csi_len, RAW_CSI_PAYLOAD_SIZE);
    return;
  }
  
  // Build flags byte
  uint8_t flags = 0;
  if (gain_locked) flags |= RAW_CSI_FLAG_GAIN_LOCKED;
  
  // Send binary packet
  if (!send_binary_packet_(csi_data, csi_len, timestamp, channel, flags)) {
    packets_dropped_++;
    if (packets_dropped_ % 100 == 1) {
      ESP_LOGW(TAG, "Dropped %u packets (network issues?)", packets_dropped_);
    }
  } else {
    packets_sent_++;
  }
}

bool RawCSIStreamer::send_binary_packet_(const int8_t* csi_data, size_t csi_len, uint32_t timestamp,
                                         uint8_t channel, uint8_t flags) {
  // Build header
  uint8_t *buf = packet_buffer_;
  
  // Magic number (4 bytes, little-endian)
  buf[0] = RAW_CSI_MAGIC & 0xFF;
  buf[1] = (RAW_CSI_MAGIC >> 8) & 0xFF;
  buf[2] = (RAW_CSI_MAGIC >> 16) & 0xFF;
  buf[3] = (RAW_CSI_MAGIC >> 24) & 0xFF;
  
  // Timestamp (4 bytes, little-endian)
  buf[4] = timestamp & 0xFF;
  buf[5] = (timestamp >> 8) & 0xFF;
  buf[6] = (timestamp >> 16) & 0xFF;
  buf[7] = (timestamp >> 24) & 0xFF;
  
  // Sequence (2 bytes, little-endian)
  buf[8] = sequence_ & 0xFF;
  buf[9] = (sequence_ >> 8) & 0xFF;
  
  // Channel (1 byte)
  buf[10] = channel;
  
  // Flags (1 byte)
  buf[11] = flags;
  
  // Copy CSI payload (128 bytes)
  memcpy(&buf[RAW_CSI_HEADER_SIZE], csi_data, csi_len);
  
  // Send via UDP using lwIP socket API
  int sock_fd = ::socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
  if (sock_fd < 0) {
    ESP_LOGE(TAG, "Failed to create socket: err=%d", sock_fd);
    return false;
  }
  
  // Set send timeout (non-blocking)
  struct timeval tv;
  tv.tv_sec = 0;
  tv.tv_usec = 10000;  // 10ms timeout
  ::setsockopt(sock_fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));
  
  // Send packet
  ssize_t sent = ::sendto(sock_fd, packet_buffer_, RAW_CSI_PACKET_SIZE, 0,
                         (struct sockaddr*)&server_addr_, sizeof(server_addr_));
  
  ::close(sock_fd);
  
  if (sent != RAW_CSI_PACKET_SIZE) {
    ESP_LOGW(TAG, "Send failed: sent=%zd, expected=%u", sent, RAW_CSI_PACKET_SIZE);
    return false;
  }
  
  // Increment sequence (wrap at 65535)
  sequence_++;
  
  return true;
}

void RawCSIStreamer::set_server(const std::string &server_ip, uint16_t server_port) {
  server_ip_ = server_ip;
  server_port_ = server_port;
  socket_initialized_ = false;  // Force re-initialization
  
  ESP_LOGI(TAG, "Server updated: %s:%u", server_ip_.c_str(), server_port_);
}

void RawCSIStreamer::set_interval(uint32_t interval_ms) {
  if (interval_ms < 2) {
    interval_ms = 2;
  } else if (interval_ms > 1000) {
    interval_ms = 1000;
  }
  
  interval_ms_ = interval_ms;
  ESP_LOGI(TAG, "Interval updated: %ums (%.1f Hz)", interval_ms_, 1000.0f / interval_ms_);
}

}  // namespace espectre
}  // namespace esphome
