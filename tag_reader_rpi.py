#!/usr/bin/env python3
import RPi.GPIO as GPIO
from mfrc522 import SimpleMFRC522
import time
from datetime import datetime, timedelta
import csv
import os
import json
import threading
import requests
import traceback
import sys

# ======= CONFIG =======
API_URL = os.getenv("ACCESS_API_URL", "http://192.168.0.100:5000")  # ajustar
API_TOKEN = os.getenv("ACCESS_API_TOKEN", "")  # se usar autenticação, coloque "Bearer <token>" ou só o token conforme API
COLLAB_CACHE_FILE = "collab_cache.json"
PENDING_FILE = "pending_logs.json"
FLUSH_INTERVAL = 20  # segundos entre tentativas de reenviar pendentes
# ======================

# Configuração dos pinos GPIO
LED_VERDE = 17
LED_VERMELHO = 27
BUZZER = 22  # Pino do buzzer

GPIO.setmode(GPIO.BCM)
GPIO.setup(LED_VERDE, GPIO.OUT)
GPIO.setup(LED_VERMELHO, GPIO.OUT)
GPIO.setup(BUZZER, GPIO.OUT)

# Configurar PWM para o buzzer
buzzer_pwm = GPIO.PWM(BUZZER, 1000)  # Frequência inicial de 1000 Hz

leitorRfid = SimpleMFRC522()

# Base de dados de colaboradores autorizados (carregada da API ou do cache)
colaboradores = {
    # valores iniciais opcionais; será substituído por cache/API em init
    2677980090: {"nome": "Joao Silva", "autorizado": True},
    219403520343: {"nome": "Maria Santos", "autorizado": False},
}

# Controle de presença e acessos
presenca_sala = {}
historico_diario = {}
tentativas_negadas = {}
tentativas_invasao = 0

# Lista para armazenar todos os eventos para o CSV
eventos_log = []

# Lock para thread-safe nos arquivos pendentes e os dados em memória
lock = threading.Lock()
stop_event = threading.Event()

# ------------------ Som e LEDs (mantidos) ------------------
def tocar_som_autorizado():
    buzzer_pwm.start(50)
    buzzer_pwm.ChangeFrequency(523)
    time.sleep(0.15)
    buzzer_pwm.ChangeDutyCycle(0)
    time.sleep(0.05)
    buzzer_pwm.ChangeDutyCycle(50)
    buzzer_pwm.ChangeFrequency(659)
    time.sleep(0.15)
    buzzer_pwm.ChangeDutyCycle(0)

def tocar_som_negado():
    buzzer_pwm.start(50)
    buzzer_pwm.ChangeFrequency(587)
    time.sleep(0.2)
    buzzer_pwm.ChangeDutyCycle(0)
    time.sleep(0.05)
    buzzer_pwm.ChangeDutyCycle(50)
    buzzer_pwm.ChangeFrequency(440)
    time.sleep(0.3)
    buzzer_pwm.ChangeDutyCycle(0)

def tocar_alarme_invasao():
    buzzer_pwm.start(50)
    for i in range(10):
        buzzer_pwm.ChangeFrequency(800)
        time.sleep(0.15)
        buzzer_pwm.ChangeFrequency(400)
        time.sleep(0.15)
    buzzer_pwm.ChangeDutyCycle(0)

def acender_led_verde():
    GPIO.output(LED_VERDE, GPIO.HIGH)
    time.sleep(5)
    GPIO.output(LED_VERDE, GPIO.LOW)

def acender_led_vermelho():
    GPIO.output(LED_VERMELHO, GPIO.HIGH)
    time.sleep(5)
    GPIO.output(LED_VERMELHO, GPIO.LOW)

def piscar_led_vermelho():
    for _ in range(10):
        GPIO.output(LED_VERMELHO, GPIO.HIGH)
        time.sleep(0.3)
        GPIO.output(LED_VERMELHO, GPIO.LOW)
        time.sleep(0.3)

# ------------------ Utilitários de cache/pending ------------------
def save_collab_cache():
    try:
        with lock:
            with open(COLLAB_CACHE_FILE, 'w', encoding='utf-8') as f:
                json.dump(colaboradores, f, ensure_ascii=False, indent=2)
    except Exception:
        print("Erro ao salvar cache de colaboradores:", traceback.format_exc())

def load_collab_cache():
    global colaboradores
    if os.path.exists(COLLAB_CACHE_FILE):
        try:
            with open(COLLAB_CACHE_FILE, 'r', encoding='utf-8') as f:
                colaboradores = {int(k):v for k,v in json.load(f).items()}
                print(f"[cache] Carregado {len(colaboradores)} colaboradores do arquivo.")
        except Exception:
            print("Erro ao ler cache de colaboradores:", traceback.format_exc())

def save_pending(pending_list):
    try:
        with lock:
            with open(PENDING_FILE, 'w', encoding='utf-8') as f:
                json.dump(pending_list, f, ensure_ascii=False, indent=2, default=str)
    except Exception:
        print("Erro ao salvar pending logs:", traceback.format_exc())

def load_pending():
    if os.path.exists(PENDING_FILE):
        try:
            with open(PENDING_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            print("Erro ao ler pending logs:", traceback.format_exc())
    return []

# ------------------ Integração com API ------------------
def fetch_collaborators_from_api():
    global colaboradores
    url = f"{API_URL}/collaborators"
    headers = {}
    if API_TOKEN:
        headers["Authorization"] = API_TOKEN
    try:
        r = requests.get(url, headers=headers, timeout=5)
        if r.status_code == 200:
            arr = r.json()
            with lock:
                colaboradores = {}
                for c in arr:
                    # badge_id pode ser string ou int; fazemos int quando possível
                    try:
                        badge = int(c.get("badge_id"))
                    except Exception:
                        badge = c.get("badge_id")
                    colaboradores[int(badge)] = {
                        "nome": c.get("name") or c.get("nome") or c.get("username") or "Sem Nome",
                        "autorizado": True if c.get("permission_level",1) >= 1 else False
                    }
            save_collab_cache()
            print(f"[api] Sincronizado {len(colaboradores)} colaboradores.")
            return True
        else:
            print(f"[api] Erro ao buscar colaboradores: {r.status_code} {r.text}")
    except Exception:
        print("[api] Exceção ao buscar colaboradores:", traceback.format_exc())
    return False

def push_log_to_api(log):
    url = f"{API_URL}/logs"
    headers = {"Content-Type":"application/json"}
    if API_TOKEN:
        headers["Authorization"] = API_TOKEN
    try:
        r = requests.post(url, json=log, headers=headers, timeout=5)
        if r.status_code in (200,201):
            return True
        else:
            print(f"[api] push_log resposta: {r.status_code} - {r.text}")
    except Exception:
        print("[api] Exceção ao enviar log:", traceback.format_exc())
    return False

# ------------------ Eventos e persistência local (CSV) ------------------
def registrar_evento(tipo, tag_id, nome="Desconhecido", autorizado=None, resultado=""):
    evento = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "tipo_evento": tipo,
        "tag_id": tag_id,
        "nome": nome,
        "autorizado": autorizado,
        "resultado": resultado
    }
    with lock:
        eventos_log.append(evento)
    # tentar enviar imediatamente
    log_for_api = {
        "badge_id": tag_id,
        "event_type": tipo,
        "result": "GRANTED" if autorizado else "DENIED",
        "reason": resultado
    }
    if not push_log_to_api(log_for_api):
        # salvar pendente
        pending = load_pending()
        pending.append(log_for_api)
        save_pending(pending)

# ------------------ Presença / lógica original (mantida) ------------------
def registrar_entrada(tag_id, nome):
    if tag_id not in presenca_sala:
        presenca_sala[tag_id] = {"dentro": False, "entrada": None, "tempo_total": timedelta(0)}
    presenca_sala[tag_id]["dentro"] = True
    presenca_sala[tag_id]["entrada"] = datetime.now()

def registrar_saida(tag_id):
    if tag_id in presenca_sala and presenca_sala[tag_id]["dentro"]:
        entrada = presenca_sala[tag_id]["entrada"]
        tempo_sessao = datetime.now() - entrada
        presenca_sala[tag_id]["tempo_total"] += tempo_sessao
        presenca_sala[tag_id]["dentro"] = False
        presenca_sala[tag_id]["entrada"] = None

def processar_acesso(tag_id):
    global tentativas_invasao
    try:
        # Tag não cadastrada - possível invasão
        if tag_id not in colaboradores:
            print("\n" + "="*50)
            print("⚠️  ALERTA DE SEGURANÇA!")
            print("Identificação não encontrada!")
            print("="*50 + "\n")
            tentativas_invasao += 1
            registrar_evento("INVASAO", tag_id, "Desconhecido", False, "Tag não cadastrada")
            tocar_alarme_invasao()
            piscar_led_vermelho()
            return

        colaborador = colaboradores[tag_id]
        nome = colaborador["nome"]
        autorizado = colaborador["autorizado"]

        # Colaborador não autorizado
        if not autorizado:
            print("\n" + "="*50)
            print(f"❌ Você não tem acesso a este projeto, {nome}")
            print("="*50 + "\n")
            if tag_id not in tentativas_negadas:
                tentativas_negadas[tag_id] = 0
            tentativas_negadas[tag_id] += 1
            registrar_evento("ACESSO_NEGADO", tag_id, nome, False, "Colaborador sem autorização")
            tocar_som_negado()
            acender_led_vermelho()
            return

        # Colaborador autorizado - verificar se está entrando ou saindo
        if tag_id not in presenca_sala or not presenca_sala[tag_id]["dentro"]:
            primeira_vez_hoje = tag_id not in historico_diario
            if primeira_vez_hoje:
                print("\n" + "="*50)
                print(f"✅ Bem-vindo, {nome}")
                print("="*50 + "\n")
                historico_diario[tag_id] = True
                registrar_evento("ENTRADA", tag_id, nome, True, "Primeira entrada do dia")
            else:
                print("\n" + "="*50)
                print(f"✅ Bem-vindo de volta, {nome}")
                print("="*50 + "\n")
                registrar_evento("ENTRADA", tag_id, nome, True, "Retorno à sala")
            registrar_entrada(tag_id, nome)
            tocar_som_autorizado()
            acender_led_verde()
        else:
            print("\n" + "="*50)
            print(f"👋 Até logo, {nome}")
            print("="*50 + "\n")
            tempo_sessao = datetime.now() - presenca_sala[tag_id]["entrada"]
            minutos = int(tempo_sessao.total_seconds() // 60)
            registrar_evento("SAIDA", tag_id, nome, True, f"Permaneceu {minutos} minutos")
            registrar_saida(tag_id)
            tocar_som_autorizado()
            acender_led_verde()
    except Exception:
        print("Erro em processar_acesso:", traceback.format_exc())

# ------------------ Export CSV (mantido) ------------------
def exportar_csv():
    timestamp_arquivo = datetime.now().strftime("%Y%m%d_%H%M%S")
    nome_arquivo = f"relatorio_acesso_{timestamp_arquivo}.csv"
    if not os.path.exists("relatorios"):
        os.makedirs("relatorios")
    caminho_completo = os.path.join("relatorios", nome_arquivo)
    with open(caminho_completo, 'w', newline='', encoding='utf-8') as csvfile:
        fieldnames = ['timestamp', 'tipo_evento', 'tag_id', 'nome', 'autorizado', 'resultado']
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        with lock:
            for evento in eventos_log:
                writer.writerow(evento)
    nome_resumo = f"resumo_acesso_{timestamp_arquivo}.csv"
    caminho_resumo = os.path.join("relatorios", nome_resumo)
    with open(caminho_resumo, 'w', newline='', encoding='utf-8') as csvfile:
        fieldnames = ['tag_id', 'nome', 'tempo_total_horas', 'tempo_total_minutos', 'tentativas_negadas']
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        for tag_id, dados in presenca_sala.items():
            if dados["dentro"]:
                tempo_sessao = datetime.now() - dados["entrada"]
                tempo_final = dados["tempo_total"] + tempo_sessao
            else:
                tempo_final = dados["tempo_total"]
            nome = colaboradores.get(tag_id, {}).get("nome", "Desconhecido")
            horas = int(tempo_final.total_seconds() // 3600)
            minutos = int((tempo_final.total_seconds() % 3600) // 60)
            tentativas = tentativas_negadas.get(tag_id, 0)
            writer.writerow({
                'tag_id': tag_id,
                'nome': nome,
                'tempo_total_horas': horas,
                'tempo_total_minutos': minutos,
                'tentativas_negadas': tentativas
            })
    print(f"\n📄 Relatórios exportados: {caminho_completo} e {caminho_resumo}")
    return caminho_completo, caminho_resumo

def gerar_relatorio():
    print("\n" + "="*60)
    print("📊 RELATÓRIO FINAL DO DIA")
    print("="*60)
    if presenca_sala:
        for tag_id, dados in presenca_sala.items():
            if dados["dentro"]:
                tempo_sessao = datetime.now() - dados["entrada"]
                tempo_final = dados["tempo_total"] + tempo_sessao
            else:
                tempo_final = dados["tempo_total"]
            nome = colaboradores.get(tag_id, {}).get("nome", "Desconhecido")
            horas = int(tempo_final.total_seconds() // 3600)
            minutos = int((tempo_final.total_seconds() % 3600) // 60)
            segundos = int(tempo_final.total_seconds() % 60)
            print(f"  • {nome}: {horas}h {minutos}m {segundos}s")
    else:
        print("  Nenhum colaborador registrado hoje.")
    print("\n🚫 TENTATIVAS DE ACESSO NÃO AUTORIZADAS:")
    if tentativas_negadas:
        for tag_id, tentativas in tentativas_negadas.items():
            nome = colaboradores.get(tag_id, {}).get("nome", "Desconhecido")
            print(f"  • {nome}: {tentativas} tentativa(s)")
    else:
        print("  Nenhuma tentativa de acesso negada.")
    print("\n⚠️  TENTATIVAS DE INVASÃO:")
    print(f"  Total de tentativas com tags não cadastradas: {tentativas_invasao}")
    print("\n💾 Exportando relatórios em CSV...")
    exportar_csv()
    print("\nSistema encerrado com sucesso!")

# ------------------ Thread que tenta reenviar pendentes ------------------
def pending_flush_worker():
    while not stop_event.is_set():
        try:
            pending = load_pending()
            if pending:
                print(f"[flush] Tentando reenviar {len(pending)} logs pendentes...")
                remaining = []
                for log in pending:
                    ok = push_log_to_api(log)
                    if not ok:
                        remaining.append(log)
                if remaining:
                    save_pending(remaining)
                else:
                    try:
                        os.remove(PENDING_FILE)
                    except Exception:
                        pass
            # tentar atualizar colaboradores periodicamente também (se a API estiver ok)
            fetch_collaborators_from_api()
        except Exception:
            print("[flush] erro no worker:", traceback.format_exc())
        # aguarda
        stop_event.wait(FLUSH_INTERVAL)

# ------------------ Programa principal ------------------
def main_loop():
    try:
        load_collab_cache()
        # tenta sincronizar com API; se falhar, usa cache
        fetch_collaborators_from_api()
        # inicia thread de flush
        t = threading.Thread(target=pending_flush_worker, daemon=True)
        t.start()

        print("\n" + "="*60)
        print("🎮 SISTEMA DE CONTROLE DE ACESSO - ESTÚDIO DE GAMES (RPI)")
        print("="*60)
        print("Aproxime o crachá do leitor para registrar entrada/saída")
        print("Pressione Ctrl+C para encerrar e ver o relatório")
        print("="*60 + "\n")

        tag_anterior = None
        tempo_ultimo_acesso = None

        while True:
            print("⏳ Aguardando leitura da tag...")
            tag_id, text = leitorRfid.read()  # bloqueante
            agora = time.time()
            # debounce
            if tag_id == tag_anterior and tempo_ultimo_acesso and (agora - tempo_ultimo_acesso) < 3:
                continue
            tag_anterior = tag_id
            tempo_ultimo_acesso = agora
            processar_acesso(tag_id)
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n\n🛑 Encerrando sistema...")
    except Exception:
        print("Erro inesperado no main loop:", traceback.format_exc())
    finally:
        # sinaliza thread para parar e aguarda um pouco
        stop_event.set()
        time.sleep(1)
        # tenta reenviar pendentes antes de sair
        try:
            pending = load_pending()
            if pending:
                print(f"[shutdown] Tentando reenviar {len(pending)} logs pendentes antes de sair...")
                remaining = []
                for log in pending:
                    ok = push_log_to_api(log)
                    if not ok:
                        remaining.append(log)
                if remaining:
                    save_pending(remaining)
                else:
                    try:
                        os.remove(PENDING_FILE)
                    except Exception:
                        pass
        except Exception:
            print("[shutdown] Erro ao flush final:", traceback.format_exc())

        gerar_relatorio()
        buzzer_pwm.stop()
        GPIO.cleanup()
        print("GPIO limpo. Sistema encerrado.")

if __name__ == "__main__":
    main_loop()
