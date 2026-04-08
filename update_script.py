import requests
import json
import os

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
        # إرسال البيانات دفعة واحدة لضمان السرعة ومنع التكرار
        response = requests.post(endpoint, json=records, headers=headers, timeout=60)
        return response.status_code in [200, 201]
    except:
        return False

def populate_crypto_table():
    print("⏳ جاري سحب البيانات من بينانس وفلترة العملات (> 1$)...")
    
    binance_url = "https://api.binance.com/api/v3/ticker/24hr"
    try:
        res = requests.get(binance_url, timeout=30)
        data = res.json()
    except Exception as e:
        print(f"❌ خطأ: {e}")
        return

    # فلترة عملات USDT فقط كما في كودك تماماً
    usdt_pairs = [coin for coin in data if coin['symbol'].endswith('USDT')]
    
    records = []
    for coin in usdt_pairs:
        try:
            price = float(coin['lastPrice'])
            
            # 🔥 شرطك الأساسي: فوق الـ 1 دولار
            if price < 1.0: 
                continue
                
            change_percent = float(coin['priceChangePercent'])
            
            # نفس الأعمدة والتحويل لـ int اللي شغال عندك 100%
            records.append({
                "symbol": coin['symbol'],
                "name": coin['symbol'].replace("USDT", ""),
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

    # الترتيب حسب الحجم لنأخذ الأهم
    records.sort(key=lambda x: x['volume_24h'], reverse=True)
    
    total_to_upload = len(records)
    print(f"🚀 تم العثور على {total_to_upload} عملة. جاري الرفع بنظامك القديم...")
    
    # الرفع بنظام الدفعات لضمان عدم حدوث Timeout
    batch_size = 30
    for i in range(0, total_to_upload, batch_size):
        batch = records[i:i + batch_size]
        if manual_upsert("crypto_market_simulation", batch):
            print(f"✅ تم تحديث الدفعة: {min(i + batch_size, total_to_upload)} / {total_to_upload}")
        else:
            print(f"⚠️ فشل في الدفعة رقم {i}")

    print(f"\n🎉 انتهى التحديث! بياناتك الآن في سوبابيس (بجدولك القديم وبنفس الطريقة).")

if __name__ == "__main__":
    populate_crypto_table()
