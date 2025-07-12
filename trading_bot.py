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
import math

class TradingBot:
    def __init__(self, log_callback: Optional[Callable] = None) -> None:
        self.log_callback = log_callback
        self.config = self._load_config()
        
        api_key = os.environ.get('BINANCE_API_KEY')
        api_secret = os.environ.get('BINANCE_API_SECRET')
        
        if not api_key or not api_secret:
            self._log("HATA: Sunucu ortam değişkenlerinde API anahtarları bulunamadı!")
            raise ValueError("API anahtarları eksik.")
            
        self.is_testnet = 'testnet' in self.config['BINANCE']['api_url']
        self.client = Client(api_key, api_secret, testnet=self.is_testnet)
        
        self.running: bool = True
        self.strategy_active: bool = False
        self.position_open: bool = False
        
        self.leverage = int(self.config['TRADING']['leverage'])
        self.quantity_usd = float(self.config['TRADING']['quantity_usd'])
        self.active_symbol = self.config['TRADING']['symbol']
        self.risk_management_mode = self.config['TRADING']['risk_management_mode']
        self.fixed_roi_tp = self.config['TRADING'].getfloat('fixed_roi_tp', 2.0) / 100
        self.active_strategy_name = self.config['TRADING']['active_strategy']
        
        self._log("Bot objesi başarıyla oluşturuldu.")

    def _load_config(self) -> configparser.ConfigParser:
        parser = configparser.ConfigParser()
        parser.read('config.ini', encoding='utf-8')
        return parser

    def _log(self, message: str) -> None:
        log_message = f"{time.strftime('%H:%M:%S')} - {message}"
        print(log_message)
        if self.log_callback:
            self.log_callback(log_message)

    def get_all_usdt_symbols(self) -> List[str]:
        try:
            info = self.client.futures_exchange_info()
            symbols = [s['symbol'] for s in info['symbols'] if s['symbol'].endswith('USDT') and 'BUSD' not in s['symbol']]
            return sorted(symbols)
        except Exception as e:
            self._log(f"HATA: Sembol listesi çekilemedi: {e}"); return []

    def update_active_symbol(self, new_symbol: str):
        if self.strategy_active:
            self._log("UYARI: Strateji çalışırken sembol değiştirilemez."); return
        self.active_symbol = new_symbol.upper()
        self._log(f"✅ Aktif sembol {self.active_symbol} olarak ayarlandı.")

    def get_current_position_data(self) -> Optional[dict]:
        try:
            all_positions = self.client.futures_account()['positions']
            position = next((p for p in all_positions if float(p['positionAmt']) != 0 and p['symbol'] == self.active_symbol), None)
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
        except Exception: return None

    def get_active_strategy_signal(self, df: pd.DataFrame) -> tuple:
        if self.active_strategy_name == 'Scalper':
            return strategy_scalper.get_signal(df, self.config['STRATEGY_Scalper'])
        else:
            return strategy_kadir_v2.get_signal(df, self.config['STRATEGY_KadirV2'])

    def _get_market_data(self, symbol: str, timeframe: str) -> Optional[pd.DataFrame]:
        try:
            klines = self.client.futures_klines(symbol=symbol, interval=timeframe, limit=200)
            df = pd.DataFrame(klines, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'quote_asset_volume', 'number_of_trades', 'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'])
            numeric_cols = ['open', 'high', 'low', 'close', 'volume']
            df[numeric_cols] = df[numeric_cols].apply(pd.to_numeric, errors='coerce')
            return df
        except Exception as e:
            self._log(f"HATA: Piyasa verileri çekilemedi: {e}"); return None

    def calculate_quantity(self) -> Optional[float]:
        symbol = self.active_symbol
        trade_usd = self.quantity_usd
        try:
            price_info = self.client.futures_mark_price(symbol=symbol)
            current_price = float(price_info['markPrice'])
            
            if trade_usd < 5.1:
                 self._log(f"UYARI: İşlem büyüklüğü ({trade_usd:.2f}$) çok düşük. Minimum ~5 USDT olmalıdır."); return None

            quantity = trade_usd / current_price
            
            info = self.client.futures_exchange_info()
            symbol_info = next(item for item in info['symbols'] if item['symbol'] == symbol)
            
            min_qty, step_size = 0.0, 0.1
            for f in symbol_info['filters']:
                if f['filterType'] == 'LOT_SIZE':
                    min_qty = float(f['minQty']); step_size = float(f['stepSize']); break
            
            if quantity < min_qty:
                self._log(f"UYARI: Hesaplanan miktar ({quantity:.4f}) bu coin için minimum ({min_qty})'dan daha az."); return None

            precision = int(round(-math.log(step_size, 10), 0)) if step_size > 0 else 0
            return round(quantity, precision)
        except Exception as e:
            self._log(f"HATA: Miktar hesaplanamadı: {e}"); return None
            
    def open_position(self, signal: str, atr: float, quantity: float, manual: bool = False) -> None:
        symbol = self.active_symbol
        side = SIDE_BUY if signal == 'LONG' else SIDE_SELL
        try:
            self.set_leverage(self.leverage, symbol)
            log_prefix = "[MANUEL]" if manual else "[STRATEJİ]"
            self._log(f"{log_prefix} {signal} sinyali için pozisyon açılıyor...")
            self.client.futures_create_order(symbol=symbol, side=side, type=ORDER_TYPE_MARKET, quantity=quantity)
            self._log(f"{log_prefix} --- POZİSYON AÇILDI: {signal} {quantity} {symbol} ---")
            
            time.sleep(1)
            account_info = self.client.futures_account()
            position = next((p for p in account_info['positions'] if p['symbol'] == symbol), None)
            entry_price = float(position['entryPrice']) if position else 0
            if entry_price == 0: self._log("HATA: Giriş fiyatı alınamadı, TP/SL ayarlanamıyor."); return

            self._log(f"Giriş Fiyatı: {entry_price}")

            if self.risk_management_mode == 'fixed_roi':
                sl_ratio = self.fixed_roi_tp / 2
                if signal == 'LONG':
                    tp_price = entry_price * (1 + self.fixed_roi_tp); sl_price = entry_price * (1 - sl_ratio)
                else:
                    tp_price = entry_price * (1 - self.fixed_roi_tp); sl_price = entry_price * (1 + sl_ratio)
                self._log(f"Sabit %{self.fixed_roi_tp*100:.2f} ROI hedefine göre hedefler belirlendi.")
            else:
                strategy_config = self.config[f"STRATEGY_{self.active_strategy_name}"]
                atr_multiplier_sl = float(strategy_config['atr_multiplier_sl'])
                atr_multiplier_tp = float(strategy_config.get('atr_multiplier_tp', atr_multiplier_sl * 2))
                sl_distance = atr * atr_multiplier_sl; tp_distance = atr * atr_multiplier_tp
                if signal == 'LONG':
                    sl_price = entry_price - sl_distance; tp_price = entry_price + tp_distance
                else:
                    sl_price = entry_price + sl_distance; tp_price = entry_price - tp_distance
                self._log(f"Dinamik ATR hedeflerine göre hedefler belirlendi.")
            
            info = self.client.futures_exchange_info()
            symbol_info = next(item for item in info['symbols'] if item['symbol'] == symbol)
            price_precision = int(symbol_info['pricePrecision'])
            sl_price, tp_price = round(sl_price, price_precision), round(tp_price, price_precision)

            self._log(f"Hedefler -> Kâr Al: {tp_price}, Zarar Durdur: {sl_price}")
            close_side = SIDE_SELL if signal == 'LONG' else SIDE_BUY
            self.client.futures_create_order(symbol=symbol, side=close_side, type=FUTURE_ORDER_TYPE_TAKE_PROFIT_MARKET, stopPrice=tp_price, closePosition=True)
            self.client.futures_create_order(symbol=symbol, side=close_side, type=FUTURE_ORDER_TYPE_STOP_MARKET, stopPrice=sl_price, closePosition=True)
            self._log("TP ve SL emirleri başarıyla yerleştirildi.")
        except Exception as e:
            self._log(f"HATA: Pozisyon açma hatası - {e}")

    def check_and_update_pnl(self, symbol: str) -> None:
        try:
            trades_in_db = database.get_all_trades()
            last_db_trade_id = max([int(t[2]) for t in trades_in_db if t[1] == symbol], default=0)
            binance_trades = self.client.futures_account_trades(symbol=symbol, limit=50)
            
            new_trades_found = False
            for trade in binance_trades:
                if int(trade['id']) > last_db_trade_id:
                    if float(trade['realizedPnl']) != 0:
                        database.add_trade(trade)
                        pnl = float(trade['realizedPnl'])
                        self._log(f"KAPALI İŞLEM: {'✅ KÂR' if pnl > 0 else '❌ ZARAR'}: {pnl:.2f} USDT.")
                        new_trades_found = True
            if new_trades_found and self.log_callback:
                self.log_callback("history_update")
        except Exception as e:
            self._log(f"HATA: PNL kontrol edilemedi: {e}")

    def close_current_position(self, from_emergency_button=False) -> None:
        symbol = self.active_symbol
        if from_emergency_button: self._log("!!! ACİL KAPATMA SİNYALİ ALINDI !!!")
        try:
            self.client.futures_cancel_all_open_orders(symbol=symbol)
            self._log(f"[{symbol}] için tüm bekleyen emirler iptal edildi.")
            account_info = self.client.futures_account()
            position = next((p for p in account_info['positions'] if p['symbol'] == symbol), None)
            if position and float(position['positionAmt']) != 0:
                pos_amount = float(position['positionAmt'])
                side = SIDE_SELL if pos_amount > 0 else SIDE_BUY
                quantity = abs(pos_amount)
                self.client.futures_create_order(symbol=symbol, side=side, type=ORDER_TYPE_MARKET, quantity=quantity)
                self._log("POZİSYON PİYASA EMRİ İLE KAPATILDI.")
            else: self._log("Kapatılacak açık pozisyon bulunamadı.")
        except Exception as e:
            self._log(f"HATA: Pozisyon kapatılırken hata oluştu: {e}")
        finally:
            time.sleep(1)
            self.check_and_update_pnl(symbol)

    def set_leverage(self, leverage: int, symbol: str):
        try:
            self.client.futures_change_leverage(symbol=symbol, leverage=leverage)
            self.leverage = leverage
            self._log(f"✅ Kaldıraç ({symbol}) {leverage}x olarak ayarlandı.")
        except Exception as e:
            self._log(f"❌ HATA: Kaldıraç ayarlanamadı: {e}")

    def set_quantity(self, quantity_usd: float):
        if quantity_usd >= 5:
            self.quantity_usd = quantity_usd
            self._log(f"✅ İşlem miktarı ~{quantity_usd} USDT olarak ayarlandı.")
        else:
            self._log("❌ HATA: Miktar en az 5 USDT olmalıdır.")

    def manual_trade(self, side: str):
        self._log(f"Manuel işlem talebi alındı: {side}")
        strategy_config = self.config[f"STRATEGY_{self.active_strategy_name}"]
        timeframe = strategy_config['timeframe']
        df = self._get_market_data(self.active_symbol, timeframe)
        if df is None: return
        _, atr_value = self.get_active_strategy_signal(df)
        quantity = self.calculate_quantity()
        if quantity:
            self.open_position(side, atr_value, quantity, manual=True)

    def set_strategy(self, strategy_name: str):
        if self.strategy_active:
            self._log("UYARI: Strateji çalışırken değiştirilemez. Lütfen önce durdurun.")
            return
        if strategy_name in ['KadirV2', 'Scalper']:
            self.active_strategy_name = strategy_name
            self._log(f"✅ Aktif strateji: {strategy_name}")
        else:
            self._log(f"❌ Geçersiz strateji adı: {strategy_name}")

    def run_strategy(self):
        self.strategy_active = True
        self._log(f"Otomatik strateji ({self.active_strategy_name}) çalıştırıldı. Sembol: {self.active_symbol}")
        self.check_and_update_pnl(self.active_symbol)
        
        while self.strategy_active:
            try:
                account_info = self.client.futures_account()
                position = next((p for p in account_info['positions'] if p['symbol'] == self.active_symbol), None)
                pos_amount = float(position['positionAmt']) if position else 0.0

                if pos_amount == 0 and self.position_open:
                    self._log("Pozisyon kapandı.")
                    self.client.futures_cancel_all_open_orders(symbol=self.active_symbol)
                    self._log(f"[{self.active_symbol}] için artık açık emir kalmadı.")
                    self.position_open = False
                    self.check_and_update_pnl(self.active_symbol)
                
                self.position_open = pos_amount != 0
                
                strategy_config = self.config[f"STRATEGY_{self.active_strategy_name}"]
                timeframe = strategy_config['timeframe']
                
                df = self._get_market_data(self.active_symbol, timeframe)
                if df is None or df.empty:
                    time.sleep(15); continue

                signal, atr_value = self.get_active_strategy_signal(df)
                self._log(f"[{self.active_symbol} | {self.active_strategy_name}] Sinyal: {signal}")

                is_long = pos_amount > 0
                is_short = pos_amount < 0

                if (is_long and signal == 'SHORT') or (is_short and signal == 'LONG'):
                    self.close_current_position()
                    continue 

                if not is_long and not is_short and signal in ['LONG', 'SHORT']:
                    quantity = self.calculate_quantity()
                    if quantity:
                        self.open_position(signal, atr_value, quantity)
                
                time.sleep(30)
            except RequestException as e:
                self._log(f"AĞ HATASI: {e}. İnternetinizi kontrol edin.")
                time.sleep(60)
            except Exception as e:
                self._log(f"ANA DÖNGÜ HATASI: {type(e).__name__} - {e}")
                time.sleep(60)
        
        self._log("Otomatik strateji motoru durduruldu.")
    
    def start_strategy_loop(self):
        if not self.strategy_active:
            self.strategy_active = True
            threading.Thread(target=self.run_strategy, daemon=True).start()

    def stop_strategy_loop(self):
        self.strategy_active = False

    def stop_all(self):
        self.running = False
        self.strategy_active = False
