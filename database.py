import sqlite3
from typing import List, Dict, Any, Tuple
import time

# Veritabanı dosyasının adı
DB_NAME = 'trades.db'

def create_connection():
    """Veritabanı bağlantısı oluşturur veya mevcut olana bağlanır."""
    conn = None
    try:
        conn = sqlite3.connect(DB_NAME, check_same_thread=False)
    except sqlite3.Error as e:
        print(f"Veritabanı bağlantı hatası: {e}")
    return conn

def init_db():
    """'trades' tablosunu, eğer mevcut değilse, oluşturur."""
    conn = create_connection()
    if conn is not None:
        try:
            cursor = conn.cursor()
            # trade_id'nin benzersiz (UNIQUE) olması, aynı işlemin tekrar eklenmesini engeller.
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
            print("Veritabanı tablosu başarıyla kontrol edildi/oluşturuldu.")
        except sqlite3.Error as e:
            print(f"Tablo oluşturma hatası: {e}")
        finally:
            conn.close()

def add_trade(trade_data: Dict[str, Any]):
    """Veritabanına yeni bir tamamlanmış işlem ekler."""
    conn = create_connection()
    if conn is not None:
        sql = ''' INSERT OR IGNORE INTO trades(symbol, trade_id, side, pnl, timestamp)
                  VALUES(?,?,?,?,?) '''
        try:
            cursor = conn.cursor()
            cursor.execute(sql, (
                trade_data['symbol'],
                int(trade_data['id']),
                trade_data['side'],
                float(trade_data['realizedPnl']),
                int(trade_data['time'])
            ))
            conn.commit()
        except sqlite3.Error as e:
            print(f"İşlem ekleme hatası: {e}")
        finally:
            conn.close()

def get_all_trades() -> List[Tuple]:
    """Tüm işlem kayıtlarını veritabanından en yeniden eskiye doğru çeker."""
    conn = create_connection()
    if conn is not None:
        try:
            cursor = conn.cursor()
            # Sütun sırasını kodlarımızla uyumlu hale getiriyoruz
            cursor.execute("SELECT id, symbol, trade_id, side, pnl, timestamp FROM trades ORDER BY timestamp DESC")
            rows = cursor.fetchall()
            return rows
        except sqlite3.Error as e:
            print(f"İşlemleri getirme hatası: {e}")
            return []
        finally:
            conn.close()
    return []

def calculate_stats() -> Dict[str, Any]:
    """Veritabanındaki verilere göre performans istatistikleri hesaplar."""
    trades = get_all_trades()
    if not trades:
        return {"total_pnl": 0, "win_rate": 0, "total_trades": 0, "wins": 0, "losses": 0}

    # Veritabanı sırasına göre pnl 4. index'te (0'dan başlayarak)
    total_pnl = sum(trade[4] for trade in trades)
    wins = sum(1 for trade in trades if trade[4] > 0)
    total_trades = len(trades)
    losses = total_trades - wins
    win_rate = (wins / total_trades) * 100 if total_trades > 0 else 0
    
    return {
        "total_pnl": total_pnl,
        "win_rate": win_rate,
        "total_trades": total_trades,
        "wins": wins,
        "losses": losses
    }

# Bu dosya ilk kez import edildiğinde, veritabanının ve tablonun var olduğundan emin ol
init_db()