from binance.client import Client
from typing import Optional

def find_most_volatile_coin(api_key: str, api_secret: str, testnet: bool) -> Optional[str]:
    """
    Binance Futures'taki son 24 saatte en çok yüzde değişimi yaşamış
    USDT paritesini bulur.

    Args:
        api_key (str): Kullanıcının Binance API anahtarı.
        api_secret (str): Kullanıcının Binance gizli anahtarı.
        testnet (bool): Testnet'e bağlanılıp bağlanılmayacağını belirtir.

    Returns:
        Optional[str]: En hareketli coinin sembolünü (örn: 'BTCUSDT') veya 
                       bir hata durumunda None döndürür.
    """
    try:
        # Geçici bir Binance istemcisi oluştur
        client = Client(api_key, api_secret, testnet=testnet)
        
        # Tüm vadeli işlem paritelerinin son 24 saatlik verilerini çek
        tickers = client.futures_ticker()
        
        # Sadece USDT ile biten ve BUSD gibi istenmeyenleri içermeyen pariteleri filtrele
        usdt_tickers = [
            ticker for ticker in tickers 
            if ticker['symbol'].endswith('USDT') and 'BUSD' not in ticker['symbol']
        ]
        
        if not usdt_tickers:
            print("Tarayıcı: Uygun USDT paritesi bulunamadı.")
            return None
            
        # Fiyat değişim yüzdesine göre en hareketli coini bul
        # abs() fonksiyonu ile hem pozitif hem de negatif yöndeki en büyük değişime bakıyoruz
        most_volatile_coin = max(usdt_tickers, key=lambda x: abs(float(x['priceChangePercent'])))
        
        # En hareketli coinin sembolünü döndür
        return most_volatile_coin['symbol']
        
    except Exception as e:
        # Bir hata oluşursa (örn: ağ hatası), konsola yazdır ve None döndür
        print(f"Coin tarayıcı hatası: {e}")
        return None
