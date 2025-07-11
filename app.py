import os
import threading
import time
from queue import Queue

from flask import (Flask, flash, jsonify, redirect, render_template, request,
                   session, url_for)

from trading_bot import TradingBot

# --- UYGULAMA KURULUMU ---
app = Flask(__name__)
# Bu anahtarı Render.com'da Environment Variable olarak ayarlayacaksınız
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'yerel_test_icin_rastgele_bir_anahtar_12345')

# --- GÜVENLİK ---
# Bu bilgileri Render.com'da Environment Variable olarak ayarlayacaksınız
ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME', 'admin')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'admin123')

# --- BOT YÖNETİMİ ---
# Bu global değişkenler, web sunucusunun bot objesini ve loglarını hafızada tutmasını sağlar
bot: TradingBot = None
log_queue = Queue()
bot_thread: threading.Thread = None

def log_handler(message):
    """Bot motorundan gelen logları arayüz için sıraya (queue) alır."""
    log_queue.put(message)

# --- WEB SAYFALARI (ROUTES) ---

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Kullanıcı giriş sayfasını yönetir."""
    if request.method == 'POST':
        if request.form['username'] == ADMIN_USERNAME and request.form['password'] == ADMIN_PASSWORD:
            session['logged_in'] = True
            global bot
            # Bot objesi sadece ilk başarılı girişte bir kez oluşturulur
            if bot is None:
                try:
                    bot = TradingBot(log_callback=log_handler)
                except ValueError as e:
                    flash(str(e))
                    return render_template('login.html')
            return redirect(url_for('dashboard'))
        else:
            flash('Geçersiz kullanıcı adı veya şifre')
    return render_template('login.html')

@app.route('/')
def index():
    """Ana sayfayı giriş sayfasına yönlendirir."""
    return redirect(url_for('login'))

@app.route('/dashboard')
def dashboard():
    """Ana kontrol panelini gösterir. Giriş yapılmamışsa login'e yönlendirir."""
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    return render_template('dashboard.html')

@app.route('/logout')
def logout():
    """Kullanıcı oturumunu sonlandırır."""
    session.pop('logged_in', None)
    return redirect(url_for('login'))

# --- KONTROL İÇİN API ENDPOINTS (JavaScript tarafından çağrılır) ---

@app.route('/start_bot', methods=['POST'])
def start_bot():
    """Arayüzden gelen 'Başlat' komutunu işler."""
    if not session.get('logged_in'): return jsonify({"status": "error", "message": "Yetkisiz"}), 401
    if bot and not bot.strategy_active:
        # Botun ana strateji döngüsünü ayrı bir thread'de başlatır
        threading.Thread(target=bot.run_strategy, daemon=True).start()
    return jsonify({"status": "success"})

@app.route('/stop_bot', methods=['POST'])
def stop_bot():
    """Arayüzden gelen 'Durdur' komutunu işler."""
    if not session.get('logged_in'): return jsonify({"status": "error"}), 401
    if bot:
        bot.stop_strategy_loop()
    return jsonify({"status": "success"})

@app.route('/get_status')
def get_status():
    """Arayüzü beslemek için tüm anlık verileri (loglar, pozisyon durumu vb.) döndürür."""
    if not session.get('logged_in'): return jsonify({"status": "error"}), 401
    
    logs = []
    while not log_queue.empty():
        logs.append(log_queue.get())
    
    if bot:
        return jsonify({
            "logs": logs,
            "bot_status": bot.strategy_active,
            "active_symbol": bot.active_symbol,
            "position": bot.get_current_position_data()
        })
    # Bot henüz oluşmadıysa varsayılan boş değerleri döndür
    return jsonify({"logs": logs, "bot_status": False, "active_symbol": "N/A", "position": None})

@app.route('/get_all_symbols')
def get_all_symbols():
    """Binance'ten tüm USDT paritelerini çeker ve arayüze gönderir."""
    if not session.get('logged_in'): return jsonify({"status": "error"}), 401
    if bot:
        symbols = bot.get_all_usdt_symbols()
        return jsonify({"symbols": symbols})
    return jsonify({"symbols": []})

@app.route('/update_symbol', methods=['POST'])
def update_symbol():
    """Arayüzden seçilen yeni sembolü botta aktif hale getirir."""
    if not session.get('logged_in'): return jsonify({"status": "error"}), 401
    data = request.get_json()
    new_symbol = data.get('symbol')
    if bot and new_symbol:
        bot.update_active_symbol(new_symbol)
        return jsonify({"status": "success"})
    return jsonify({"status": "error", "message": "Sembol güncellenemedi."}), 400

@app.route('/manual_trade', methods=['POST'])
def manual_trade():
    """Arayüzden gelen manuel işlem talebini işler."""
    if not session.get('logged_in'): return jsonify({"status": "error"}), 401
    data = request.get_json()
    side = data.get('side')
    if bot and side in ['LONG', 'SHORT']:
        threading.Thread(target=bot.manual_trade, args=(side,), daemon=True).start()
        return jsonify({"status": "success"})
    return jsonify({"status": "error", "message": "Geçersiz işlem yönü."}), 400

@app.route('/close_position', methods=['POST'])
def close_position():
    """Açık pozisyonu kapatma komutunu işler."""
    if not session.get('logged_in'): return jsonify({"status": "error"}), 401
    if bot:
        threading.Thread(target=bot.close_current_position, args=(True,), daemon=True).start()
    return jsonify({"status": "success"})

@app.route('/update_settings', methods=['POST'])
def update_settings():
    """Arayüzden gelen kaldıraç ve miktar ayarlarını günceller."""
    if not session.get('logged_in'): return jsonify({"status": "error"}), 401
    data = request.get_json()
    if bot:
        try:
            leverage = int(data.get('leverage'))
            quantity_usd = float(data.get('quantity_usd'))
            bot.set_leverage(leverage, bot.active_symbol)
            bot.set_quantity(quantity_usd)
            return jsonify({"status": "success"})
        except (ValueError, TypeError):
            return jsonify({"status": "error", "message": "Geçersiz değerler."}), 400
    return jsonify({"status": "error", "message": "Bot aktif değil."}), 400

if __name__ == '__main__':
    # Bu kısım sadece yerel testler içindir. 
    # Render.com gibi sunucular bu bloğu çalıştırmaz, onun yerine 'gunicorn' kullanır.
    app.run(debug=False, host='0.0.0.0', port=8080)
