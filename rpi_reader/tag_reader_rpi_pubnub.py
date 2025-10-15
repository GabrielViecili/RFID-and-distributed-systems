# mesmo início do script anterior; importe seu AsyncConn e inicialize publisher
from pubsub import AsyncConn
PUB = AsyncConn("RPI Reader", "access_channel")  # mantém mesmo canal da API
# ... dentro de registrar_evento(), depois de push_log_to_api/log saved:
try:
    PUB.publish({"badge_id": tag_id, "event_type": tipo, "result": "GRANTED" if autorizado else "DENIED", "reason": resultado, "ts": datetime.utcnow().isoformat()})
except Exception:
    pass
# (o restante do código é semelhante ao sqlite version; só adicione a publish)
