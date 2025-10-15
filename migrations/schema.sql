-- colaboradores
CREATE TABLE IF NOT EXISTS collaborators (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  badge_id TEXT UNIQUE NOT NULL,  -- id da tag
  name TEXT NOT NULL,
  role TEXT,
  permission_level INTEGER DEFAULT 1,
  username TEXT UNIQUE,
  password_hash TEXT
);

-- logs de acesso
CREATE TABLE IF NOT EXISTS access_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  badge_id TEXT,
  event_type TEXT,        -- "ENTRY", "EXIT", "ATTEMPT"
  result TEXT,            -- "GRANTED" ou "DENIED"
  reason TEXT,
  timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- tokens simples (opcional)
CREATE TABLE IF NOT EXISTS api_tokens (
  token TEXT PRIMARY KEY,
  username TEXT,
  expires_at DATETIME
);
