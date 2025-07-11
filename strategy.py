import pandas as pd
import pandas_ta as ta
import configparser
from typing import Tuple

def get_signal(df: pd.DataFrame, config: configparser.SectionProxy) -> Tuple[str, float]:
    """
    Daha dengeli işlem yapmak için tasarlanmış, EMA kesişimi ve RSI onayına dayalı
    "KadirV2 Agresif" momentum stratejisi.

    Args:
        df (pd.DataFrame): Mum verilerini içeren DataFrame.
        config (configparser.SectionProxy): Strateji ayarlarını içeren config bölümü ([STRATEGY_KadirV2]).

    Returns:
        Tuple[str, float]: ('Sinyal', ATR Değeri) -> ('LONG', 0.0025)
    """
    # --- Strateji parametrelerini config'den oku ---
    ema_fast_len = int(config['ema_length_fast'])
    ema_slow_len = int(config['ema_length_slow'])
    rsi_len = int(config['rsi_length'])
    rsi_ob = int(config['rsi_overbought'])
    rsi_os = int(config['rsi_oversold'])
    atr_len = int(config['atr_length'])

    # --- Gerekli tüm indikatörleri hesapla ---
    df.ta.ema(length=ema_fast_len, append=True)
    df.ta.ema(length=ema_slow_len, append=True)
    df.ta.rsi(length=rsi_len, append=True)
    df.ta.atr(length=atr_len, append=True)
    
    # İndikatör sütunlarının isimlerini belirle
    ema_fast_col = f"EMA_{ema_fast_len}"
    ema_slow_col = f"EMA_{ema_slow_len}"
    rsi_col = f"RSI_{rsi_len}"
    atr_col = f"ATRr_{atr_len}"

    # Karşılaştırma için son iki mumu al
    latest = df.iloc[-2]
    prev = df.iloc[-3]

    # --- Sinyal Koşullarını Belirle ---

    # 1. EMA Kesişimi Koşulu: Hızlı EMA, yavaş EMA'yı yeni kesmiş olmalı.
    ema_bull_cross = latest[ema_fast_col] > latest[ema_slow_col] and prev[ema_fast_col] <= prev[ema_slow_col]
    ema_bear_cross = latest[ema_fast_col] < latest[ema_slow_col] and prev[ema_fast_col] >= prev[ema_slow_col]

    # 2. RSI Onay Koşulu: Fiyatın aşırı bölgeden 'dönüyor' olması.
    rsi_confirm_long = latest[rsi_col] > rsi_os
    rsi_confirm_short = latest[rsi_col] < rsi_ob
    
    # --- Nihai Sinyali Oluştur ---
    # Eğer bir yükseliş kesişimi varsa VE RSI aşırı satım bölgesinden çıkmışsa LONG sinyali ver.
    if ema_bull_cross and rsi_confirm_long:
        return 'LONG', latest[atr_col]
    
    # Eğer bir düşüş kesişimi varsa VE RSI aşırı alım bölgesinden çıkmışsa SHORT sinyali ver.
    if ema_bear_cross and rsi_confirm_short:
        return 'SHORT', latest[atr_col]
        
    # Eğer hiçbir koşul sağlanmıyorsa BEKLE.
    return 'WAIT', 0
