# Raw CSI Streaming - Documentação de Modificações

## Visão Geral

Esta documentação descreve as modificações realizadas no ESPectre para adicionar suporte ao streaming de dados CSI (Channel State Information) crus via UDP, permitindo coleta de dados das 64 subportadoras completas para alimentação de modelos LLM.

## Objetivo

Transformar o ESPectre de um sistema exclusivo de detecção de movimento para um coletor de dados CSI crus que:
- Captura todas as 64 subportadoras (não apenas 12 selecionadas)
- Envia dados via WiFi em formato binário compacto
- Mantém compatibilidade com detecção de movimento existente
- É configurável via YAML com taxa de 2-500Hz (padrão 100Hz)

---

## Arquivos Criados

### 1. `components/espectre/raw_csi_streamer.h`

**Propósito:** Header da classe `RawCSIStreamer` responsável por enviar dados CSI brutos via UDP.

**Estrutura do Pacote Binário (140 bytes):**
```
Offset  Size  Description
-----   ----  -----------
0       4     Magic: 0x43535241 ("CSRA" = CSI Raw ASCII)
4       4     Timestamp: uint32 (milissegundos desde boot)
8       2     Sequence: uint16 (contador de pacotes)
10      1     Channel: uint8 (canal WiFi)
11      1     Flags: uint8 (bit 0 = gain_locked, bit 1 = stbc)
12      128   CSI Data: int8[128] (64 subcarriers × 2: I/Q interleaved)
```

**Métodos Principais:**
- `init(server_ip, server_port, interval_ms)` - Inicializa streamer
- `start()` / `stop()` - Controla streaming
- `send_packet(csi_data, csi_len, timestamp, channel, gain_locked)` - Envia pacote
- `set_server()` / `set_interval()` - Configuração dinâmica

**Constantes Definidas:**
- `RAW_CSI_MAGIC = 0x43535241`
- `RAW_CSI_HEADER_SIZE = 12`
- `RAW_CSI_PAYLOAD_SIZE = 128`
- `RAW_CSI_PACKET_SIZE = 140`

---

### 2. `components/espectre/raw_csi_streamer.cpp`

**Propósito:** Implementação do streamer com socket UDP não-bloqueante.

**Funcionalidades:**
- Socket UDP com timeout de 10ms
- Construção de pacotes binários em buffer circular
- Envio periódico controlado por timestamp
- Estatísticas de pacotes enviados/dropados
- Re-resolução de DNS para reconnect automático

**Tratamento de Erros:**
- Validação de tamanho de dados CSI (deve ser 128 bytes)
- Clamp de intervalo entre 2-1000ms
- Log de drop rate a cada 100 pacotes perdidos

---

## Arquivos Modificados

### 3. `components/espectre/espectre.h`

**Adições:**
```cpp
// Include do novo header
#include "raw_csi_streamer.h"

// Setter methods
void set_raw_csi_enabled(bool enabled);
void set_raw_csi_server_ip(const std::string &ip);
void set_raw_csi_server_port(uint16_t port);
void set_raw_csi_interval(uint32_t interval_ms);

// Membros da classe
RawCSIStreamer raw_csi_streamer_;
bool raw_csi_enabled_{false};
std::string raw_csi_server_ip_;
uint16_t raw_csi_server_port_{5001};
uint32_t raw_csi_interval_ms_{10};  // 10ms = 100Hz
```

**Por que:** Adiciona suporte a configuração e controle do streamer de dados crus.

---

### 4. `components/espectre/espectre.cpp`

**Modificações em `setup()`:**
```cpp
// Inicialização condicional do streamer
if (this->raw_csi_enabled_) {
    this->raw_csi_streamer_.init(
      this->raw_csi_server_ip_,
      this->raw_csi_server_port_,
      this->raw_csi_interval_ms_
    );
}
```

**Modificações em `on_wifi_connected_()`:**
```cpp
// Callback para enviar dados crus a cada pacote CSI
if (this->raw_csi_enabled_) {
    this->csi_manager_.set_raw_csi_callback(
      [this](const int8_t* csi_data, size_t csi_len, uint32_t timestamp) {
        uint8_t channel = 0;
        bool gain_locked = this->csi_manager_.is_gain_locked();
        
        wifi_ap_record_t ap_info;
        if (esp_wifi_sta_get_ap_info(&ap_info) == ESP_OK) {
            channel = ap_info.primary;
        }
        
        this->raw_csi_streamer_.send_packet(
            csi_data, csi_len, timestamp, channel, gain_locked
        );
      }
    );
    
    this->raw_csi_streamer_.start();
}
```

**Modificações em `on_wifi_disconnected_()`:**
```cpp
// Parada limpa do streamer
if (this->raw_csi_streamer_.is_running()) {
    this->raw_csi_streamer_.stop();
}
```

**Por que:** Integra o streamer ao ciclo de vida do componente, garantindo inicialização após WiFi conectar e parada ao desconectar.

---

### 5. `components/espectre/csi_manager.h`

**Adições:**
```cpp
// Novo tipo de callback para dados crus
using raw_csi_callback_t = std::function<void(const int8_t*, size_t, uint32_t)>;

// Método para configurar callback
void set_raw_csi_callback(raw_csi_callback_t callback);

// Membro para armazenar callback
raw_csi_callback_t raw_csi_callback_;
```

**Por que:** Permite que outros componentes recebam dados CSI completos antes do processamento para detecção.

---

### 6. `components/espectre/csi_manager.cpp`

**Modificações em `process_packet()`:**
```cpp
// Envia dados crus ANTES de qualquer filtragem
if (raw_csi_callback_) {
    uint32_t timestamp = esp_timer_get_time() / 1000;  // ms
    raw_csi_callback_(data->buf, data->len, timestamp);
}

// Processamento normal continua...
```

**Por que:** Garante que dados completos das 64 subportadoras sejam capturados antes de qualquer seleção de bandas para detecção.

**Observação:** O callback é chamado mesmo durante gain lock, permitindo calibração completa.

---

### 7. `components/espectre/__init__.py`

**Novas Constantes:**
```python
CONF_RAW_CSI_ENABLED = "raw_csi_enabled"
CONF_RAW_CSI_SERVER_IP = "raw_csi_server_ip"
CONF_RAW_CSI_SERVER_PORT = "raw_csi_server_port"
CONF_RAW_CSI_INTERVAL = "raw_csi_interval"
```

**Configurações no Schema:**
```yaml
raw_csi_enabled: true/false (default: false)
raw_csi_server_ip: "192.168.1.100" (default: "")
raw_csi_server_port: 5001 (default: 5001, range: 1-65535)
raw_csi_interval: 10 (default: 10ms, range: 2-1000ms)
```

**Código de Geração:**
```python
# Configura streamer se habilitado
cg.add(var.set_raw_csi_enabled(config[CONF_RAW_CSI_ENABLED]))
if config[CONF_RAW_CSI_ENABLED]:
    cg.add(var.set_raw_csi_server_ip(config[CONF_RAW_CSI_SERVER_IP]))
    cg.add(var.set_raw_csi_server_port(config[CONF_RAW_CSI_SERVER_PORT]))
    cg.add(var.set_raw_csi_interval(config[CONF_RAW_CSI_INTERVAL]))
```

**Por que:** Expõe configuração via YAML para usuários ESPHome.

---

## Exemplo de Configuração YAML

### Configuração Básica (apenas coleta)

```yaml
espectre:
  raw_csi_enabled: true
  raw_csi_server_ip: "192.168.1.100"
  raw_csi_server_port: 5001
  raw_csi_interval: 10  # 100Hz
```

### Configuração Completa (coleta + detecção)

```yaml
espectre:
  # Detecção de movimento (mantém funcionalidade original)
  segmentation_threshold: auto
  segmentation_window_size: 75
  detection_algorithm: mvs
  
  movement_sensor:
    name: "Movement Score"
  motion_sensor:
    name: "Motion Binary"
  
  # Coleta de dados CSI crus (NOVO)
  raw_csi_enabled: true
  raw_csi_server_ip: "192.168.1.100"
  raw_csi_server_port: 5001
  raw_csi_interval: 10  # 100Hz (2-1000ms)
```

---

## Fluxo de Dados

```
ESP32-C6                          Servidor Recebedor
     |                                  |
     |  CSI RX (64 subcarriers)         |
     |→ WiFi CSI Interface              |
     |         ↓                        |
     |    CSI Manager                   |
     |         ↓                        |
     |    [DOIS CAMINHOS]               |
     |         ↓                        |
     |    Detector (12 SC) → Home Assistant
     |         ↓                        |
     |    Raw CSI Streamer              |
     |         ↓                        |
     |    UDP Binary (140 bytes) ───────→ Receber → Salvar
     |         @ 100Hz                  |
     |                                  |
```

---

## Performance e Recursos

### Uso de Rede
- **Tamanho do pacote:** 140 bytes
- **Taxa padrão (100Hz):** 14 KB/s = 112 Kbps
- **Taxa máxima (500Hz):** 70 KB/s = 560 Kbps
- **Overhead WiFi:** ~20% (frame headers)
- **Uso total @ 100Hz:** ~135 Kbps

### Uso de Memória ESP32
- **Buffer de pacote:** 140 bytes
- **Socket UDP:** ~2KB
- **Código adicional:** ~8KB flash
- **Heap adicional:** ~4KB

### Latência
- **Processamento CSI:** <1ms
- **Construção pacote:** <0.1ms
- **Envio UDP:** 1-5ms (depende da rede)
- **Latência total:** ~5-10ms

---

## Servidor de Recebimento (Exemplo Python)

```python
import socket
import struct

MAGIC = 0x43535241
HEADER_FORMAT = '<IIBB'  # magic, timestamp, seq, channel, flags
HEADER_SIZE = 12
CSI_SIZE = 128
PACKET_SIZE = 140

def receive_csi(server_port=5001):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(('0.0.0.0', server_port))
    
    print(f'Esperando dados CSI na porta {server_port}...')
    
    while True:
        data, addr = sock.recvfrom(PACKET_SIZE)
        
        if len(data) != PACKET_SIZE:
            print(f'Pacote inválido: {len(data)} bytes')
            continue
        
        # Parse header
        magic, timestamp, seq, channel, flags = struct.unpack(
            HEADER_FORMAT, data[:HEADER_SIZE]
        )
        
        if magic != MAGIC:
            print(f'Magic inválido: 0x{magic:08X}')
            continue
        
        # Parse CSI data
        csi_data = list(struct.unpack('<128b', data[HEADER_SIZE:]))
        
        # Extrair flags
        gain_locked = bool(flags & 0x01)
        stbc = bool(flags & 0x02)
        
        print(f'Seq: {seq}, Channel: {channel}, '
              f'Gain: {gain_locked}, CSI: {len(csi_data)} samples')

if __name__ == '__main__':
    receive_csi()
```

---

## Considerações de Implementação

### Por que UDP e não TCP?
- **Menor latência:** Sem handshakes
- **Sem retransmissão:** Pacotes perdidos são aceitáveis
- **Simplicidade:** Menos overhead de código

### Por que formato binário?
- **Compacto:** 140 bytes vs ~1000 bytes JSON
- **Rápido:** Sem parsing necessário
- **Eficiente:** Menos tráfego de rede

### Por que callback no CSI Manager?
- **Dados crus:** Captura antes de qualquer filtragem
- **Flexível:** Múltiplos consumidores possíveis
- **Eficiente:** Zero-copy quando possível

---

## Testes Recomendados

### 1. Verificar Conexão WiFi
```bash
# No ESP32
esphome logs espectre.yaml

# Deve mostrar:
# [I][raw_csi_streamer]: Raw CSI Streamer initialized
# [I][raw_csi_streamer]:   Server: 192.168.1.100:5001
# [I][raw_csi_streamer]:   Interval: 10ms (100.0 Hz)
# [I][espectre]: Raw CSI streaming enabled
```

### 2. Verificar Envio de Pacotes
```python
# No servidor Python
# Deve mostrar sequência crescente de pacotes
Seq: 1, Channel: 6, Gain: True, CSI: 128 samples
Seq: 2, Channel: 6, Gain: True, CSI: 128 samples
...
```

### 3. Verificar Taxa de Perda
```
# No log ESP32 após alguns minutos
[W][raw_csi_streamer]: Dropped 5 packets (network issues?)

# Se >1% perda, verificar:
# - Sinal WiFi (RSSI deve ser >-70)
# - Congestionamento de rede
# - Capacidade do servidor
```

---

## Troubleshooting

### Problema: "Failed to resolve server IP"
**Causa:** IP inválido ou DNS não funcionando
**Solução:** Usar IP direto em vez de hostname

### Problema: Pacotes dropados em alta taxa
**Causa:** Rede congestionada ou servidor lento
**Solução:** 
- Reduzir intervalo (ex: 10ms → 20ms)
- Verificar RSSI do WiFi
- Usar canal WiFi menos congestionado

### Problema: Magic inválido no servidor
**Causa:** Pacote corrompido ou formato errado
**Solução:** 
- Verificar se packet size = 140 bytes
- Confirmar endianness little-endian

---

## Próximos Passos

1. ✅ Implementação do streamer C++ (COMPLETO)
2. ⏳ Servidor Python de recebimento (a desenvolver)
3. ⏳ Integração com TimescaleDB
4. ⏳ Buffer local no ESP32 para offline (opcional)
5. ⏳ Compressão de dados (opcional)

---

## Referências

- [ESP-IDF WiFi CSI Documentation](https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-guides/wifi.html#wi-fi-channel-state-information-csi)
- [ESP-csi Project](https://github.com/espressif/esp-csi)
- [ESPectre Original Repository](https://github.com/francescopace/espectre)

---

**Versão:** 1.0  
**Data:** Março 2026  
**Autor:** Modificações por solicitação do usuário  
**Branch:** `raw_csi`
