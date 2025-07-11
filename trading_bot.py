import os
import configparser
import time
import pandas as pd
from binance.client import Client
from binance.enums import *
import strategy as strategy_kadir_v2
import strategy_scalper
import database
import screener
from typing import Callable, Optional, List
from requests.exceptions import RequestException
import threading

class TradingBot:
    """
    Web arayüzü ile kontrol edilen, sunucuda 7/24 çalışmak üzere tasarlanmış,
    gelişmiş ticaret botu motoru.
    """
    def __init__(self, log_callback: Optional[Callable] = None) -> None:
        self.log_callback = log_callback
        self.config = self._load_config()
        
        # GÜVENLİK: API anahtarlarını ortam değişkenlerinden (sunucudan) oku
        api_key = os.environ.get('BINANCE_API_KEY')
        api_secret = os.environ.get('BINANCE_API_SECRET')
        
        if not api_key or not api_secret:
            self._log("HATA: Sunucu ortam değişkenlerinde BINANCE_API_KEY ve BINANCE_API_SECRET bulunamadı!")
            raise ValueError("API anahtarları eksik.")
            
        self.is_testnet = 'testnet' in self.config['BINANCE']['api_url']
        self.client = Client(api_key, api_secret, testnet=self.is_testnet)
        
        # Botun durumunu ve ayarlarını tutan değişkenler
        self.strategy_active: bool = False
        self.position_open: bool = False
        self.active_symbol = self.config['TRADING']['symbol']
        self.active_strategy_name = self.config['TRADING']['active_strategy']
        
        self._log("Bot objesi başarıyla oluşturuldu.")

    def _load_config(self) -> configparser.ConfigParser:
        """config.ini dosyasını yükler."""
        parser = configparser.ConfigParser()
        parser.read('config.ini', encoding='utf-8')
        return parser

    def _log(self, message: str) -> None:
        """Log mesajlarını hem sunucu konsoluna yazar hem de web arayüzüne gönderir."""
        log_message = f"{time.strftime('%H:%M:%S')} - {message}"
        print(log_message) # Sunucu logları için
        if self.log_callback:
            self.log_callback(log_message)

    def get_all_usdt_symbols(self) -> List[str]:
        """Binance Futures'taki tüm USDT paritelerinin listesini döndürür."""
        try:
            info = self.client.futures_exchange_info()
            symbols = [s['symbol'] for s in info['symbols'] if s['symbol'].endswith('USDT') and 'BUSD' not in s['symbol']]
            return sorted(symbols)
        except Exception as e:
            self._log(f"HATA: Sembol listesi çekilemedi: {e}")
            return []

    def update_active_symbol(self, new_symbol: str):
        """Botun aktif olarak işlem yapacağı sembolü günceller."""
        if self.strategy_active:
            self.log_callback("UYARI: Strateji çalışırken sembol değiştirilemez. Lütfen önce stratejiyi durdurun.")
            return
        self.active_symbol = new_symbol.upper()
        self._log(f"✅ Aktif sembol {self.active_symbol} olarak ayarlandı.")

    def get_current_position_data(self) -> Optional[dict]:
        """Açık pozisyonun anlık verilerini arayüz için hazırlar."""
        try:
            # Sadece aktif sembol için değil, tüm açık pozisyonları kontrol et
            all_positions = self.client.futures_account()['positions']
            position = next((p for p in all_positions if float(p['positionAmt']) != 0), None)
            
            if not position: return None

            tp_sl_orders = self.client.futures_get_open_orders(symbol=position['symbol'])
            tp_order = next((o for o in tp_sl_orders if o['origType'] == 'TAKE_PROFIT_MARKET'), None)
            sl_order = next((o for o in tp_sl_orders if o['origType'] == 'STOP_MARKET'), None)
            pnl = float(position['unrealizedProfit'])
            roi = (pnl / (float(position['initialMargin']) + 1e-9)) * 100
            
            return {
                "symbol": position['symbol'], "quantity": position['positionAmt'], "entry_price": position['entryPrice'],
                "mark_price": position['markPrice'], "pnl_usdt": f"{pnl:.2f}", "roi_percent": f"{roi:.2f}",
                "sl_price": sl_order['stopPrice'] if sl_order else "N/A", "tp_price": tp_order['stopPrice'] if tp_order else "N/A",
            }
        except Exception:
            return None

    def get_active_strategy_signal(self, df: pd.DataFrame) -> tuple:
        """Config'de seçili olan stratejiyi çalıştırır."""
        if self.active_strategy_name == 'Scalper':
            return strategy_scalper.get_signal(df, self.config['STRATEGY_Scalper'])
        else: # Varsayılan: KadirV2
            return strategy_kadir_v2.get_signal(df, self.config['STRATEGY_KadirV2'])

    def _get_market_data(self, symbol: str, timeframe: str) -> Optional[pd.DataFrame]:
        """Binance'ten mum verilerini çeker ve hazırlar."""
        try:
            klines = self.client.futures_klines(symbol=symbol, interval=timeframe, limit=200)
            df = pd.DataFrame(klines, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'quote_asset_volume', 'number_of_trades', 'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'])
            numeric_cols = ['open', 'high', 'low', 'close', 'volume']
            df[numeric_cols] = df[numeric_cols].apply(pd.to_numeric, errors='coerce')
            return df
        except Exception as e:
            self._log(f"HATA: Piyasa verileri çekilemedi: {e}"); return None
            
    def run_strategy(self):
        """Sadece otomatik strateji mantığını çalıştıran ana döngü."""
        self.strategy_active = True
        self._log(f"Otomatik strateji ({self.active_strategy_name}) çalıştırıldı. Sembol: {self.active_symbol}")
        
        while self.strategy_active:
            try:
                # Pozisyon kontrolü
                position = next((p for p in self.client.futures_account()['positions'] if p['symbol'] == self.active_symbol), None)
                pos_amount = float(position['positionAmt']) if position else 0.0

                # Veri çekme ve strateji uygulama
                strategy_config = self.config[f"STRATEGY_{self.active_strategy_name}"]
                timeframe = strategy_config['timeframe']
                df = self._get_market_data(self.active_symbol, timeframe)
                if df is None or df.empty:
                    time.sleep(15); continue

                signal, atr_value = self.get_active_strategy_signal(df)
                self._log(f"[{self.active_symbol} | {self.active_strategy_name}] Sinyal: {signal}")

                # İşlem mantığı (Aç, Kapat, Tersine Çevir)
                # ... (Bu kısım önceki masaüstü versiyonundaki gibi kalacak) ...
                
                time.sleep(30)
            except RequestException as e:
                self._log(f"AĞ HATASI: {e}. İnternetinizi kontrol edin.")
                time.sleep(60)
            except Exception as e:
                self._log(f"ANA DÖNGÜ HATASI: {type(e).__name__} - {e}")
                time.sleep(60)
        
        self._log("Otomatik strateji motoru durduruldu.")

    def stop_strategy_loop(self):
        """Arayüzden gelen komutla strateji döngüsünü durdurur."""
        self._log("Strateji durduruluyor...")
        self.strategy_active = False
