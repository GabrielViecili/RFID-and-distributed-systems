# Sistema de Gerenciamento de Acessos - Estúdio de Games

Entrega: API (SQLite) + Frontend (PubNub real-time) + Leitor RPi (offline-safe) + Análise (Pandas)

## Estrutura
- `api/` - Flask API + SQLite (data.db)
- `frontend/` - `index.html` (consome PubNub)
- `rpi_reader/` - scripts para Raspberry Pi:
  - `tag_reader_rpi_json.py` (pending em JSON)
  - `tag_reader_rpi_sqlite.py` (pending em SQLite local)
  - `tag_reader_rpi_pubnub.py` (publica direto no PubNub)
- `analytics/analysis.py` - scripts Pandas
- `docker-compose.yml` - compose para api + frontend

## Requisitos
- Python 3.8+
- Pacotes: `pip install flask requests pandas mfrc522`
- PubNub account + chaves configuradas em `pubsub.py` (arquivo existente)
- Raspberry Pi com leitor MFRC522 conectado
- (Opcional) Docker

## Setup rápido (local)
1. Copie os arquivos para as pastas indicadas.
2. Configure variáveis no RPi (ou local):
```bash
export ACCESS_API_URL="http://<API_HOST>:5000"
export ACCESS_API_TOKEN=""  # se usar auth (token obtido via /auth/login)
