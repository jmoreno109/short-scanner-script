
# General Data

SELECT * FROM market_snapshots where symbol = 'NEAR' ORDER BY created_at DESC;
SELECT * FROM scanner_history WHERE symbol = 'NEAR' ORDER BY timestamp DESC;

SELECT symbol as sym, price, ROUND(rsi, 3) as rsi, rvol,  score, ROUND(oi_delta, 3) as oi_delta, ROUND(efficiency, 3) as efficiency  FROM scanner_history WHERE 1=1 
AND symbol = 'NEAR' ORDER BY timestamp DESC;

SELECT symbol as sym, price, ROUND(rsi, 3) as rsi, rvol,  score, ROUND(oi_delta, 3) as oi_delta, ROUND(efficiency, 3) as efficiency  FROM scanner_history WHERE 1=1 
AND symbol = 'HYPE' ORDER BY timestamp DESC;

SELECT symbol as sym,  price, ROUND(rsi, 3) as rsi, rvol,  score, ROUND(oi_delta, 3) as oi_delta, ROUND(efficiency, 3) as efficiency  FROM scanner_history WHERE 1=1 
AND symbol = 'XLM' ORDER BY timestamp DESC;


# Top OI spikes

SELECT symbol, oi_delta, score FROM scanner_history
WHERE oi_delta > 10 ORDER BY timestamp DESC;


# Mejores setups históricos

SELECT * FROM scanner_history WHERE score >= 8 
ORDER BY score DESC;


# Inefficiency extrema

SELECT *
FROM scanner_history
WHERE efficiency < 0.25
ORDER BY timestamp DESC;


# Detectar efficiency decay

SELECT  timestamp, efficiency, rvol
FROM scanner_history
WHERE symbol = 'HYPE'
ORDER BY timestamp DESC
LIMIT 10;


# backtesting

SELECT symbol as sym, price, ROUND(rsi, 3) as rsi, rvol,  score, ROUND(oi_delta, 3) as oi_delta, 
ROUND(efficiency, 3) as efficiency  
FROM scanner_history WHERE 1=1 AND symbol = 'NEAR' ORDER BY timestamp DESC;

SELECT symbol as sym, price, ROUND(rsi, 3) as rsi, rvol,  score, ROUND(oi_delta, 3) as oi_delta, 
ROUND(efficiency, 3) as efficiency , ROUND(oi_threshold, 3) as oi_threshold, ROUND(oi_strength, 3) as oi_strength
FROM market_snapshots WHERE 1=1 AND symbol = 'NEAR' ORDER BY created_at DESC;


# Other

drop table market_snapshots;

SELECT * FROM scanner_history ORDER BY timestamp DESC;

SELECT datetime(1779906836, 'unixepoch');

PRAGMA table_info(scanner_history);

SELECT price,timestamp FROM scanner_history
WHERE symbol = 'NEAR'
AND timestamp <= strftime('%s', 'now', '-8 hours')
ORDER BY timestamp DESC
LIMIT 1
;   


# Efficiency Index

CREATE INDEX IF NOT EXISTS idx_oi_symbol_time
ON oi_history(symbol, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_scanner_symbol_time
ON scanner_history(symbol, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_price_symbol_time
ON price_history(symbol, timestamp DESC);
