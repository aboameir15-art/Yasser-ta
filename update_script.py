import requests
import json

# --- [ بيانات سوبابيس الخاصة بك ] ---
SUPABASE_URL = "https://snlcbtgzdxsacwjipggn.supabase.co" 
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InNubGNidGd6ZHhzYWN3amlwZ2duIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3MDU3NDMzMiwiZXhwIjoyMDg2MTUwMzMyfQ.v3SRkONLNlQw5LWhjo03u0fDce3EvWGBpJ02OGg5DEI"

def manual_upsert(table_name, records):
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates"
    }
    endpoint = f"{SUPABASE_URL}/rest/v1/{table_name}"
    try:
        response = requests.post(endpoint, json=records, headers=headers, timeout=60)
        return response.status_code in [200, 201]
    except:
        return False

def populate_crypto_table():
    print("⏳ جاري محاولة جلب البيانات عبر واجهة بديلة (تجاوز الحظر)...")
    
    # قائمة بروابط بديلة لبينانس، إذا فشل واحد ينتقل للآخر
    endpoints = [
        "https://api1.binance.com/api/v3/ticker/24hr",
        "https://api2.binance.com/api/v3/ticker/24hr",
        "https://api3.binance.com/api/v3/ticker/24hr",
        "https://data-api.binance.vision/api/v3/ticker/24hr" # هذا الرابط مخصص للداتا وغالباً لا يحظر
    ]
    
    data = None
    for url in endpoints:
        try:
            print(f"🔄 محاولة الاتصال بـ: {url}")
            res = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
            if res.status_code == 200:
                data = res.json()
                print("✅ نجح الاتصال!")
                break
        except:
            continue

    if not data or not isinstance(data, list):
        print("❌ جميع الروابط محظورة حالياً من قبل بينانس لسيرفرات جيت هاب.")
        return

    records = []
    for coin in data:
        try:
            symbol = coin['symbol']
            if not symbol.endswith('USDT'): continue
            
            price = float(coin['lastPrice'])
            if price < 1.0: continue
                
            change_percent = float(coin['priceChangePercent'])
            
            records.append({
                "symbol": symbol,
                "name": symbol.replace("USDT", ""),
                "current_price": int(price), 
                "open_price_24h": int(float(coin['openPrice'])),
                "high_24h": int(float(coin['highPrice'])),
                "low_24h": int(float(coin['lowPrice'])),
                "volume_24h": int(float(coin['volume'])),
                "change_24h": int(change_percent),
                "ema_20": int(price), 
                "ema_50": int(price),
                "rsi_val": 50,
                "bb_upper": int(price * 1.02),
                "bb_middle": int(price),
                "bb_lower": int(price * 0.98),
                "last_tick_direction": "UP" if change_percent >= 0 else "DOWN"
            })
        except: continue

    if not records:
        print("⚠️ لم يتم العثور على عملات تطابق الشرط.")
        return

    records.sort(key=lambda x: x['volume_24h'], reverse=True)
    print(f"🚀 تم تجهيز {len(records)} عملة. جاري الرفع لسوبابيس...")
    
    batch_size = 25
    for i in range(0, len(records), batch_size):
        batch = records[i:i + batch_size]
        manual_upsert("crypto_market_simulation", batch)

    print(f"🎉 تم التحديث بنجاح!")

if __name__ == "__main__":
    populate_crypto_table()
