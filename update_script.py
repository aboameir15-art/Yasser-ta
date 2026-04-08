import requests
import os

# بيانات سوبابيس الخاصة بك
SUPABASE_URL = "https://snlcbtgzdxsacwjipggn.supabase.co" 
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InNubGNidGd6ZHhzYWN3amlwZ2duIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3MDU3NDMzMiwiZXhwIjoyMDg2MTUwMzMyfQ.v3SRkONLNlQw5LWhjo03u0fDce3EvWGBpJ02OGg5DEI"

def update_prices():
    print("⏳ جاري سحب البيانات من بينانس...")
    try:
        # استخدام رابط API بينانس للأسعار خلال 24 ساعة
        res = requests.get("https://api.binance.com/api/v3/ticker/24hr", timeout=30)
        data = res.json()
    except Exception as e:
        print(f"❌ خطأ في الاتصال ببينانس: {e}")
        return

    records = []
    for coin in data:
        symbol = coin['symbol']
        if not symbol.endswith('USDT'): continue
        
        try:
            price = float(coin['lastPrice'])
            
            # 🔥 شرطك الأساسي: العملات التي سعرها 1 دولار أو أكثر
            if price < 1.0: 
                continue
                
            change_percent = float(coin['priceChangePercent'])
            
            # بناء السجل بناءً على أعمدة جدولك
            records.append({
                "symbol": symbol,
                "name": symbol.replace("USDT", ""),
                "current_price": price, 
                "open_price_24h": float(coin['openPrice']),
                "high_24h": float(coin['highPrice']),
                "low_24h": float(coin['lowPrice']),
                "volume_24h": float(coin['volume']),
                "change_24h": change_percent,
                "ema_20": price, 
                "ema_50": price,
                "rsi_val": 50.0,
                "bb_upper": price * 1.02,
                "bb_middle": price,
                "bb_lower": price * 0.98,
                "last_tick_direction": "UP" if change_percent >= 0 else "DOWN"
            })
        except: continue

    if not records:
        print("⚠️ لم يتم العثور على عملات تطابق شرط الـ 1 دولار.")
        return

    print(f"🚀 تم تجهيز {len(records)} عملة. جاري التحديث في سوبابيس...")

    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        # 🔥 هذا السطر يضمن التحديث بناءً على الـ symbol ومنع التكرار
        "Prefer": "resolution=merge-duplicates"
    }
    
    endpoint = f"{SUPABASE_URL}/rest/v1/crypto_market_simulation"
    
    try:
        response = requests.post(endpoint, json=records, headers=headers)
        if response.status_code in [200, 201]:
            print("✅ تم التحديث بنجاح! اذهب وتحقق من الجدول الآن.")
        else:
            print(f"❌ فشل التحديث. كود الخطأ: {response.status_code}")
            print(f"📝 السبب: {response.text}")
    except Exception as e:
        print(f"❌ خطأ غير متوقع: {e}")

if __name__ == "__main__":
    update_prices()
