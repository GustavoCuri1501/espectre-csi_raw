# ESPectre CSI Server

Servidor Python para receber dados CSI brutos do ESP32-S3 LilyGO.

## Funcionalidades

- ✅ Recebe pacotes UDP com dados CSI (64 subcarriers)
- ✅ Validação de formato binário
- ✅ Detecção de movimento em tempo real
- ✅ Salvamento em CSV e/ou binário
- ✅ Visualizador gráfico em tempo real
- ✅ Estatísticas de recepção

## Instalação

```bash
# Criar ambiente virtual (opcional)
python3 -m venv venv
source venv/bin/activate

# Instalar dependências
pip install -r requirements.txt
```

## Uso

### 1. Servidor Básico (apenas receber e salvar)

```bash
python csi_receiver.py
```

Isso vai:
- Ouvir na porta 5001 UDP
- Salvar dados em CSV e binário na pasta `csi_data/`
- Exibir estatísticas em tempo real

### 2. Servidor com Visualização Gráfica

```bash
python visualizer.py
```

Abre uma janela mostrando:
- Amplitudes dos 64 subcarriers em tempo real
- Turbulência (métrica de movimento)
- Status de detecção de movimento
- Estatísticas de pacotes

### 3. Opções Avançadas

```bash
# Porta personalizada
python csi_receiver.py -p 5002

# Salvar apenas CSV
python csi_receiver.py --save-csv

# Salvar apenas binário
python csi_receiver.py --save-binary

# Desabilitar detecção de movimento
python csi_receiver.py --no-motion

# Diretório de saída personalizado
python csi_receiver.py -o /path/to/data

# Ajustar sensibilidade de movimento
python csi_receiver.py --window 100 --threshold 1.5
```

## Formato dos Dados

### Pacote Binário (140 bytes)

```
Offset  Size  Description
-----   ----  -----------
0       4     Magic: 0x43535241 ("CSRA")
4       4     Timestamp: uint32 (ms desde boot)
8       2     Sequence: uint16 (contador)
10      1     Channel: uint8 (canal WiFi)
11      1     Flags: uint8 (bit 0 = gain_locked)
12      128   CSI Data: int8[128] (64 subcarriers × I/Q)
```

### CSV

Colunas:
- `timestamp`: Milissegundos
- `seq`: Número de sequência
- `channel`: Canal WiFi
- `gain_locked`: 1 se gain lock ativado
- `stbc`: 1 se STBC detectado
- `ampl_0` a `ampl_63`: Amplitudes dos 64 subcarriers
- `phase_0` a `phase_63`: Fases dos 64 subcarriers

## Detecção de Movimento

O servidor implementa detecção de movimento baseada em:

1. **Turbulência Espacial**: Desvio padrão das amplitudes dos subcarriers
2. **Variance Threshold**: Variação da turbulência em uma janela deslizante

### Parâmetros

- `--window`: Tamanho da janela para cálculo (padrão: 75 pacotes)
- `--threshold`: Limiar de detecção (padrão: 1.0)

Ajuste conforme necessário:
- **Maior sensibilidade**: Diminua o threshold (ex: 0.5)
- **Menos falsos positivos**: Aumente o threshold (ex: 2.0)

## Exemplos de Uso

### Coleta de Dados para Treinamento

```bash
# Coleta contínua
python csi_receiver.py --save-csv

# Em outro terminal, faça movimentos na frente do ESP
# Os dados serão salvos em csi_data/csi_data_TIMESTAMP.csv
```

### Visualização em Tempo Real

```bash
# Inicie o visualizador
python visualizer.py

# O gráfico mostrará:
# - Topo: Amplitudes dos 64 subcarriers
# - Meio: Turbulência (linha vermelha sobe com movimento)
# - Baixo: Estatísticas
```

### Teste de Conexão

```bash
# Inicie o servidor
python csi_receiver.py

# Deve aparecer:
# [READY] Server started. Waiting for CSI data...

# Quando o ESP conectar:
# [STATS] Received:    100 | Invalid:    0 | Dropped:      0 | PPS:  100.0
```

## Configuração do ESP32

No arquivo YAML do ESPHome, configure:

```yaml
espectre:
  raw_csi_enabled: true
  raw_csi_server_ip: "192.168.42.117"  # IP do seu PC
  raw_csi_server_port: 5001
  raw_csi_interval: 10  # 100Hz
```

## Troubleshooting

### "Failed to bind socket: Address already in use"
- Outra instância do servidor está rodando
- Use `lsof -i :5001` para encontrar o processo
- Ou use uma porta diferente: `python csi_receiver.py -p 5002`

### "Invalid magic" ou muitos pacotes inválidos
- Verifique se o ESP está enviando no formato correto
- Confirme que o IP do servidor está correto no YAML

### PPS muito baixo (<50)
- Verifique sinal WiFi (RSSI deve ser >-70)
- Reduza interferência de outros dispositivos
- Aumente `raw_csi_interval` no ESP para evitar congestionamento

### Detecção de movimento muito sensível
- Aumente o threshold: `python csi_receiver.py --threshold 2.0`
- Aumente a janela: `python csi_receiver.py --window 100`

## Arquivos

```
csi_server/
├── csi_receiver.py      # Servidor principal
├── visualizer.py        # Visualizador gráfico
├── requirements.txt     # Dependências Python
└── README.md           # Este arquivo
```

## Performance

### Uso de Rede
- **Tamanho do pacote**: 140 bytes
- **Taxa padrão (100Hz)**: 14 KB/s
- **Uso com overhead WiFi**: ~17 KB/s

### Uso de CPU
- **Recebimento**: <5% (single core)
- **Visualização**: ~15-20% (depende da resolução)

## Próximos Passos

- [ ] Integração com TimescaleDB
- [ ] Exportação para formatos ML (TFRecord, HDF5)
- [ ] Detecção de gestos (não apenas movimento)
- [ ] Cliente web para visualização remota

## License

GPLv3 - Same as ESPectre
