import requests
import os
from supabase import create_client, Client

# سحب المفاتيح تلقائياً من بيئة Render أو جهازك
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')

if not SUPABASE_URL or not SUPABASE_KEY:
    print("❌ خطأ: مفاتيح سوبابيس غير موجودة! تأكد من إعداد المتغيرات.")
    exit()

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def populate_crypto_table():
    print("⏳ جاري سحب البيانات الحية من بينانس...")
    
    url = "https://api.binance.com/api/v3/ticker/24hr"
    response = requests.get(url)
    
    if response.status_code != 200:
        print("❌ فشل الاتصال ببينانس!")
        return

    data = response.json()
    usdt_pairs = [coin for coin in data if coin['symbol'].endswith('USDT')]
    usdt_pairs.sort(key=lambda x: float(x['quoteVolume']), reverse=True)
    
    top_coins = usdt_pairs[:120]
    records = []
    print(f"📊 تم اختيار أقوى {len(top_coins)} عملة، جاري التجهيز...")

    for coin in top_coins:
        price = float(coin['lastPrice'])
        price_change = float(coin['priceChangePercent'])
        
        record = {
            "symbol": coin['symbol'],
            "name": coin['symbol'].replace("USDT", ""),
            "current_price": price,
            "open_price_24h": float(coin['openPrice']),
            "high_24h": float(coin['highPrice']),
            "low_24h": float(coin['lowPrice']),
            "volume_24h": float(coin['volume']),
            "ema_20": price, 
            "ema_50": price,
            "rsi_val": 50.0, 
            "bb_upper": price * 1.05,
            "bb_middle": price,
            "bb_lower": price * 0.95,
            "last_tick_direction": "UP" if price_change >= 0 else "DOWN"
        }
        records.append(record)

    print("🚀 جاري ضخ البيانات إلى جدول crypto_market_simulation...")
    batch_size = 40
    for i in range(0, len(records), batch_size):
        batch = records[i:i + batch_size]
        supabase.table("crypto_market_simulation").upsert(batch).execute()
        print(f"✅ تم رفع دفعة {i + len(batch)} / {len(records)}")

    print("🎉 اكتملت العملية بنجاح! جدولك الآن مليء ببيانات بينانس الحقيقية.")

if __name__ == "__main__":
    populate_crypto_table()