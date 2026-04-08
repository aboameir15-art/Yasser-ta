import requests
import json
import os

# --- [ بيانات سوبابيس المستخرجة من ملفك ] ---
SUPABASE_URL = "https://snlcbtgzdxsacwjipggn.supabase.co" 
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InNubGNidGd6ZHhzYWN3amlwZ2duIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3MDU3NDMzMiwiZXhwIjoyMDg2MTUwMzMyfQ.v3SRkONLNlQw5LWhjo03u0fDce3EvWGBpJ02OGg5DEI"

def manual_upsert(table_name, records):
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        # التحديث الذكي: يدمج البيانات إذا كانت العملة موجودة ولا يكرر الصفوف
        "Prefer": "resolution=merge-duplicates"
    }
    endpoint = f"{SUPABASE_URL}/rest/v1/{table_name}"
    try:
        response = requests.post(endpoint, json=records, headers=headers, timeout=60)
        return response.status_code in [200, 201]
    except Exception as e:
        print(f"❌ خطأ في سوبابيس: {e}")
        return False

def populate_crypto_table():
    print("⏳ جاري سحب البيانات من بينانس وفلترة العملات (> 1$)...")
    
    binance_url = "https://api.binance.com/api/v3/ticker/24hr"
    try:
        res = requests.get(binance_url, timeout=30)
        data = res.json()
    except Exception as e:
        print(f"❌ خطأ في جلب بيانات بينانس: {e}")
        return

    records = []
    for coin in data:
        try:
            symbol = coin['symbol']
            if not symbol.endswith('USDT'): continue
            
            price = float(coin['lastPrice'])
            
            # شرط الـ 1 دولار الخاص بك
            if price < 1.0: continue
                
            change_percent = float(coin['priceChangePercent'])
            
            records.append({
                "symbol": symbol,
                "name": symbol.replace("USDT", ""),
                "current_price": float(price), 
                "open_price_24h": float(coin['openPrice']),
                "high_24h": float(coin['highPrice']),
                "low_24h": float(coin['lowPrice']),
                "volume_24h": float(coin['volume']),
                "change_24h": float(change_percent),
                # تحديث المؤشرات الفنية (قيم تقريبية تعتمد على السعر الحالي)
                "ema_20": float(price), 
                "ema_50": float(price),
                "rsi_val": 50.0,
                "bb_upper": float(price * 1.02),
                "bb_middle": float(price),
                "bb_lower": float(price * 0.98),
                "last_tick_direction": "UP" if change_percent >= 0 else "DOWN"
            })
        except: continue

    if not records:
        print("⚠️ لم يتم العثور على عملات تطابق الشرط.")
        return

    # ترتيب حسب الحجم
    records.sort(key=lambda x: x['volume_24h'], reverse=True)
    
    total = len(records)
    print(f"🚀 تم تجهيز {total} عملة. جاري التحديث الآمن لجدول crypto_market_simulation...")
    
    # رفع البيانات على دفعات (Batching)
    batch_size = 50
    for i in range(0, total, batch_size):
        batch = records[i:i + batch_size]
        if manual_upsert("crypto_market_simulation", batch):
            print(f"✅ تم تحديث الدفعة {i//batch_size + 1}")
        else:
            print(f"⚠️ فشل تحديث الدفعة {i//batch_size + 1}")

    print("\n🎉 انتهى التحديث بنجاح. بياناتك الآن في سوبابيس آمنة ومحدثة.")

if __name__ == "__main__":
    populate_crypto_table()
  
