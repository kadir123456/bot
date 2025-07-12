import sqlite3
from typing import List, Dict, Any, Tuple
import time

DB_NAME = 'trades.db'

def init_db():
    """Veritabanını ve 'trades' tablosunu, eğer mevcut değilse, oluşturur."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            trade_id INTEGER UNIQUE NOT NULL,
            side TEXT NOT NULL,
            pnl REAL NOT NULL,
            timestamp INTEGER NOT NULL
        );
    """)
    conn.commit()
    conn.close()
    print("Veritabanı tablosu başarıyla kontrol edildi/oluşturuldu.")

def add_trade(trade_data: Dict[str, Any]):
    """Veritabanına yeni bir tamamlanmış işlem ekler."""
    conn = sqlite3.connect(DB_NAME)
    sql = ''' INSERT OR IGNORE INTO trades(symbol, trade_id, side, pnl, timestamp)
              VALUES(?,?,?,?,?) '''
    try:
        cursor = conn.cursor()
        cursor.execute(sql, (
            trade_data['symbol'], int(trade_data['id']), trade_data['side'],
            float(trade_data['realizedPnl']), int(trade_data['time'])
        ))
        conn.commit()
    finally:
        conn.close()

def get_all_trades() -> List[Tuple]:
    """Tüm işlem kayıtlarını veritabanından en yeniden eskiye doğru çeker."""
    conn = sqlite3.connect(DB_NAME)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id, symbol, trade_id, side, pnl, timestamp FROM trades ORDER BY timestamp DESC")
        return cursor.fetchall()
    finally:
        conn.close()

def calculate_stats() -> Dict[str, Any]:
    """Veritabanındaki verilere göre performans istatistikleri hesaplar."""
    trades = get_all_trades()
    if not trades:
        return {"total_pnl": 0, "win_rate": 0, "total_trades": 0, "wins": 0, "losses": 0}

    total_pnl = sum(trade[4] for trade in trades)
    wins = sum(1 for trade in trades if trade[4] > 0)
    total_trades = len(trades)
    losses = total_trades - wins
    win_rate = (wins / total_trades) * 100 if total_trades > 0 else 0
    
    return {"total_pnl": total_pnl, "win_rate": win_rate, "total_trades": total_trades, "wins": wins, "losses": losses}

# Program ilk import edildiğinde veritabanını hazırla
init_db()
