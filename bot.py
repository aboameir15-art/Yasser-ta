import logging
import asyncio
import random
import time
import os
import json
import unicodedata
import re
import io
import difflib
import requests
import httpx  
import aiohttp
import arabic_reshaper
from datetime import datetime, timedelta
from aiogram import types
from datetime import datetime, timedelta # 💡 تمت الإضافة هنا
from aiogram import types
from aiogram.dispatcher.filters import Text 
from pilmoji import Pilmoji 
from PIL import Image, ImageDraw, ImageFont, ImageOps
from bidi.algorithm import get_display
from aiogram import Bot, Dispatcher, types, executor
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from supabase import create_client, Client

# --- [ 1. إعدادات الهوية والاتصال ] ---
ADMIN_ID = 7988144062
OWNER_USERNAME = "@Ya_79k"

# سحب التوكينات من Render (لن يعمل البوت بدونها في الإعدادات)
API_TOKEN = os.getenv('BOT_TOKEN')
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')

# --- [ استدعاء القلوب الثلاثة - تشفير خارجي ] ---
# هنا الكود يطلب المفاتيح من المتغيرات فقط، ولا توجد أي قيمة مسجلة هنا
GROQ_KEYS = [
    os.getenv('G_KEY_1'),
    os.getenv('G_KEY_2'),
    os.getenv('G_KEY_3')
]

# تصفية المصفوفة لضمان عدم وجود قيم فارغة
GROQ_KEYS = [k for k in GROQ_KEYS if k]
current_key_index = 0  # مؤشر تدوير القلوب

# التحقق من وجود المتغيرات الأساسية لضمان عدم حدوث Crash
if not API_TOKEN or not GROQ_KEYS:
    logging.error("❌ خطأ: المتغيرات المشفرة مفقودة في إعدادات Render!")

# تعريف المحركات
bot = Bot(token=API_TOKEN, parse_mode="HTML")
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
# 1. في بداية الملف (خارج كل الدوال) قم بتعريف هذا المتغير
bot_username = None 

# ==========================================
# 1. إعدادات الجلسات والقيم الثابتة (Config & State)
# ==========================================
trade_sessions = {}
LEVERAGE_LEVELS = [1, 5, 10, 20, 50, 100]
MARGIN_PCT_LEVELS = [5, 10, 25, 50, 75, 100]
DURATION_MAP = {
    '4h': ('4 ساعات', timedelta(hours=4)),
    '12h': ('12 ساعة', timedelta(hours=12)),
    '1d': ('يوم واحد', timedelta(days=1)),
    '2d': ('يومين', timedelta(days=2)),
    '1w': ('أسبوع', timedelta(weeks=1)),
    '1m': ('شهر', timedelta(days=30))
}
DURATION_KEYS = list(DURATION_MAP.keys())

# ==========================================
# 2. الدوال المالية والحسابية الأساسية (Core Logic)
# ==========================================
async def market_engine():
    """محرك تحديث الأسعار والمؤشرات تلقائياً كل دقيقة"""
    while True:
        try:
            # جلب كل العملات من الجدول
            res = supabase.table("crypto_market_simulation").select("*").execute()
            for coin in res.data:
                symbol = coin['symbol']
                old_price = float(coin['current_price'])
                
                # محاكاة حركة عشوائية (بين -2% و +2%)
                change_pct = random.uniform(-0.02, 0.02)
                new_price = old_price * (1 + change_pct)
                
                # تحديث بسيط للمؤشرات الفنية (محاكاة)
                new_rsi = max(10, min(90, float(coin.get('rsi_val', 50)) + random.uniform(-5, 5)))
                new_ema = (new_price * 0.1) + (float(coin.get('ema_50', new_price)) * 0.9)
                
                # تحديث قاعدة البيانات
                supabase.table("crypto_market_simulation").update({
                    "current_price": new_price,
                    "change_24h": ((new_price - float(coin['open_price_24h'])) / float(coin['open_price_24h'])) * 100,
                    "rsi_val": new_rsi,
                    "ema_50": new_ema,
                    "last_tick_direction": "UP" if change_pct > 0 else "DOWN"
                }).eq("symbol", symbol).execute()
                
            logging.info("✅ تم تحديث نبض السوق بنجاح.")
        except Exception as e:
            logging.error(f"❌ خطأ في محرك السوق: {e}")
            
        await asyncio.sleep(60) # تحديث كل دقيقة
        
async def trade_reaper():
    """رادار لمراقبة تصفية الصفقات وانتهاء الوقت"""
    while True:
        try:
            # جلب الصفقات النشطة فقط
            active_trades = supabase.table("active_trades").select("*").eq("is_active", True).execute()
            
            for trade in active_trades.data:
                tid = trade['id']
                uid = trade['user_id']
                sym = trade['symbol']
                side = trade['side']
                liq_price = float(trade['liquidation_price'])
                expiry = datetime.fromisoformat(trade['expiry_time'])
                
                # جلب السعر الحالي للعملة
                c_res = supabase.table("crypto_market_simulation").select("current_price").eq("symbol", sym).execute()
                current_p = float(c_res.data[0]['current_price'])
                
                # 1. فحص التصفية (Liquidation)
                is_liquidated = (side == 'LONG' and current_p <= liq_price) or \
                                (side == 'SHORT' and current_p >= liq_price)
                
                if is_liquidated:
                    await close_trade_manually(tid, liq_price) # إغلاق بسعر التصفية (صفر ربح)
                    await bot.send_message(uid, f"💔 <b>تصفية صاعقة!</b>\nتم تصفية صفقتك على #{sym} بسبب وصول السعر إلى {liq_price:,.4f}$", parse_mode="HTML")
                    continue

                # 2. فحص انتهاء الوقت (Expiration)
                if datetime.now() > expiry:
                    success, pnl = await close_trade_manually(tid, current_p)
                    msg = "💰 ربح" if pnl > 0 else "📉 خسارة"
                    await bot.send_message(uid, f"⏳ <b>انتهى وقت الصفقة!</b>\nتم إغلاق صفقة #{sym} تلقائياً.\nالنتيجة: {msg} بقيمة {pnl:+.2f}$", parse_mode="HTML")

        except Exception as e:
            logging.error(f"❌ خطأ في رادار التصفية: {e}")
            
        await asyncio.sleep(30) # فحص كل 30 ثانية
        
        
def calculate_liquidation(entry_price, leverage, side):
    """حساب دقيق لسعر التصفية""" [cite: 89]
    entry = float(entry_price)
    lev = int(leverage)
    if side == 'LONG':
        return entry * (1 - (1.0 / lev)) [cite: 89]
    else: 
        return entry * (1 + (1.0 / lev)) [cite: 89]

# ==========================================
# 1. الدوال الحسابية (Math Core)
# ==========================================

def calculate_liquidation(entry_price, leverage, side):
    """حساب سعر التصفية: السعر الذي تفقد عنده كامل الهامش"""
    entry = float(entry_price)
    lev = int(leverage)
    # المعادلة: سعر الدخول * (1 -/+ 1/الرافعة)
    if side == 'LONG':
        return entry * (1 - (1.0 / lev))
    else: 
        return entry * (1 + (1.0 / lev))

def generate_candle_chart(direction):
    """رسم توضيحي بسيط لاتجاه السعر (شمعة يابانية)"""
    if direction == 'UP':
        return "📉 ⇠ |---🟩---|\n⇠ 🚀 صعود إيجابي"
    else:
        return "📈 ⇠ |---🟥---|\n⇠ 🩸 هبوط سلبي"

# ==========================================
# 2. إدارة البيانات المالية (Database Helpers)
# ==========================================

async def get_user_data(user_id):
    """جلب بيانات المستخدم من جدول السوبابيس الرئيسي"""
    res = supabase.table("users_global_profile").select("*").eq("user_id", user_id).execute()
    return res.data[0] if res.data else None

async def check_financial_health(user_id, amount, action="WITHDRAW"):
    """نظام الحماية: يمنع السحب إذا وجد دين أو صفقات مفتوحة محجوزة"""
    data = await get_user_data(user_id)
    if not data: return False, "❌ حسابك غير مسجل."
    
    bank_bal = float(data.get('bank_balance', 0))
    debt = float(data.get('debt_balance', 0))
    
    # حساب الهامش المحجوز (Locked Margin) في الصفقات النشطة
    trades_res = supabase.table("active_trades").select("margin").eq("user_id", user_id).eq("is_active", True).execute()
    locked_margin = sum(float(t['margin']) for t in trades_res.data) if trades_res.data else 0
    available_cash = bank_bal - locked_margin

    if action == "WITHDRAW":
        if debt > 0:
            return False, f"⚠️ لا يمكنك السحب! لديك دين مستحق بقيمة {debt:,.2f} $.\nسدد ديونك أولاً."
        if amount > available_cash:
            return False, f"⚠️ المبلغ محجوز في صفقات نشطة.\nالمتاح فعلياً: {available_cash:,.2f} $."
    
    elif action == "BORROW":
        if debt > 0:
            return False, "⚠️ لديك قرض نشط.\nلا يمكنك الاقتراض مجدداً قبل السداد."
        if bank_bal < 10:
            return False, "⚠️ رصيدك ضعيف جداً للحصول على ائتمان (أقل من 10$)."
            
    return True, "Success"

# ==========================================
# 3. إدارة الصفقات النشطة (Trade Management)
# ==========================================

async def get_active_trades_report(user_id):
    """حساب الأرباح والخسائر (PnL) الحالية لكل صفقة مفتوحة"""
    res = supabase.table("active_trades").select("*").eq("user_id", user_id).eq("is_active", True).execute()
    trades = res.data
    if not trades:
        return None, "📋 <b>لا توجد صفقات مفتوحة حالياً.</b>"

    report_text = "📋 | <b>قـائمة صـفـقاتك الـمفتوحة</b>\n━━━━━━━━━━━━━━━━━━\n"
    for trade in trades:
        symbol = trade['symbol']
        side = "🟢 LONG" if trade['side'] == 'LONG' else "🔴 SHORT"
        entry = float(trade['entry_price'])
        lev = trade['leverage']
        margin = float(trade['margin'])
        
        # جلب سعر العملة الحالي من جدول محاكاة السوق
        coin_res = supabase.table("crypto_market_simulation").select("current_price").eq("symbol", symbol).execute()
        current_price = float(coin_res.data[0]['current_price']) if coin_res.data else entry

        # حساب النسبة المئوية للربح/الخسارة بناءً على الرافعة المالية
        pnl_pct = (current_price - entry) / entry if trade['side'] == 'LONG' else (entry - current_price) / entry
        pnl_amount = margin * pnl_pct * lev
        pnl_emoji = "💰" if pnl_amount >= 0 else "📉"

        report_text += f"<b>#{symbol} | {side} {lev}x</b>\n"
        report_text += f"• الـدخول: <code>{entry:,.4f}</code> | الآن: <code>{current_price:,.4f}</code>\n"
        report_text += f"{pnl_emoji} الـربح/الخسارة: <b>{pnl_amount:+.2f} $</b>\n"
        report_text += "━━━━━━━━━━━━━━━━━━\n"
        
    return trades, report_text

async def close_trade_manually(trade_id, current_price):
    """إغلاق الصفقة وتصفية الحساب وإرجاع الرصيد للبنك"""
    res = supabase.table("active_trades").select("*").eq("id", trade_id).execute()
    if not res.data: return False, "الصفقة غير موجودة."
    
    trade = res.data[0]
    user_id = trade['user_id']
    entry = float(trade['entry_price'])
    margin = float(trade['margin'])
    lev = int(trade['leverage'])
    side = trade['side']
    
    # حساب النتيجة النهائية
    pnl_pct = (current_price - entry) / entry if side == 'LONG' else (entry - current_price) / entry
    pnl_amount = margin * pnl_pct * lev
    total_return = margin + pnl_amount # الهامش الأصلي + الربح (أو - الخسارة)
    
    # تحديث رصيد البنك
    user_data = await get_user_data(user_id)
    new_bank = float(user_data['bank_balance']) + total_return
    supabase.table("users_global_profile").update({"bank_balance": new_bank}).eq("user_id", user_id).execute()
    
    # أرشفة الصفقة (جعلها غير نشطة)
    supabase.table("active_trades").update({
        "is_active": False, 
        "close_price": current_price, 
        "pnl": pnl_amount,
        "closed_at": datetime.now().isoformat()
    }).eq("id", trade_id).execute()
    
    return True, pnl_amount
# ==========================================
# 3. قوالب واجهات المستخدم (Secured Keyboards)
# ==========================================

def get_market_keyboard(user_id):
    markup = InlineKeyboardMarkup(row_width=3)
    
    # تصحيح: إضافة الفواصل بين الأزرار وحذف المراجع النصية التي تسبب الخطأ
    markup.row(
        InlineKeyboardButton("🔥 الرائجة", callback_data=f"market_tab:{user_id}:trending"),
        InlineKeyboardButton("📈 الرابحة", callback_data=f"market_tab:{user_id}:gainers"),
        InlineKeyboardButton("📉 الخاسرة", callback_data=f"market_tab:{user_id}:losers")
    )
    
    # إضافة الأزرار الرئيسية في صفوف منفصلة
    markup.add(InlineKeyboardButton("🏦 محفظتي الماليـة", callback_data=f"wallet_view:{user_id}"))
    markup.add(InlineKeyboardButton("📋 صفقاتي المفتوحة", callback_data=f"active_trades_view:{user_id}"))
    
    return markup
    
# ==========================================
# 3. قوالب واجهات المستخدم المصححة
# ==========================================

def get_coin_keyboard(user_id, symbol):
    markup = InlineKeyboardMarkup(row_width=2)
    # تصحيح: إضافة الفاصلة بين الزرين
    markup.row(
        InlineKeyboardButton("🟢 شـراء (LONG)", callback_data=f"setup_trade:{user_id}:{symbol}:LONG"),
        InlineKeyboardButton("🔴 بـيـع (SHORT)", callback_data=f"setup_trade:{user_id}:{symbol}:SHORT")
    )
    markup.add(InlineKeyboardButton("🔙 العودة للسوق", callback_data=f"market_tab:{user_id}:trending"))
    return markup

def get_trade_setup_keyboard(user_id):
    session = trade_sessions.get(user_id)
    if not session: return None
    
    sym = session['symbol']
    side = session['side']
    
    markup = InlineKeyboardMarkup(row_width=2)
    # تصحيح: إضافة الفاصلة بين الزرين
    markup.row(
        InlineKeyboardButton(f"⚖️ الرافعة: {session['leverage']}x", callback_data=f"trade_cycle:{user_id}:leverage"),
        InlineKeyboardButton(f"💼 النسبة: {session['margin_pct']}%", callback_data=f"trade_cycle:{user_id}:margin")
    )
    markup.add(InlineKeyboardButton(f"⏳ المدة: {DURATION_MAP[session['duration']][0]}", callback_data=f"trade_cycle:{user_id}:duration"))
    
    confirm_text = "🚀 تأكيد الشراء (LONG)" if side == 'LONG' else "🩸 تأكيد البيع (SHORT)"
    markup.add(InlineKeyboardButton(confirm_text, callback_data=f"trade_confirm:{user_id}:{sym}"))
    markup.add(InlineKeyboardButton("❌ إلغاء", callback_data=f"coin_view:{user_id}:{sym}"))
    return markup

def get_wallet_keyboard(user_id, debt):
    markup = InlineKeyboardMarkup(row_width=2)
    # تصحيح: إضافة الفاصلة بين الزرين
    markup.row(
        InlineKeyboardButton("📥 إيداع للتداول", callback_data=f"transfer_flow:{user_id}:to_bank"),
        InlineKeyboardButton("📤 سحب للمحفظة", callback_data=f"transfer_flow:{user_id}:to_wallet")
    )
    
    if debt > 0:
        markup.add(InlineKeyboardButton("🔴 تسديد القرض", callback_data=f"repay_loan:{user_id}"))
    else:
        markup.add(InlineKeyboardButton("💰 طلب قرض سريع", callback_data=f"loan_menu:{user_id}"))
        
    # تصحيح: إضافة الفاصلة بين الزرين في markup.add
    markup.row(
        InlineKeyboardButton("📋 صفقاتي", callback_data=f"active_trades_view:{user_id}"),
        InlineKeyboardButton("🛒 السوق", callback_data=f"market_tab:{user_id}:trending")
    )
    return markup

def get_trades_keyboard(user_id, trades):
    markup = InlineKeyboardMarkup(row_width=1) # يفضل عرض الصفقات عمودياً لسهولة التحكم
    for trade in trades:
        symbol = trade['symbol']
        trade_id = trade['id']
        # إضافة أزرار كل صفقة في صف واحد (Row)
        markup.row(
            InlineKeyboardButton(f"🚀 تعزيز {symbol}", callback_data=f"dca_trade:{user_id}:{trade_id}"),
            InlineKeyboardButton(f"❌ إغلاق", callback_data=f"close_trade:{user_id}:{trade_id}")
        )
    markup.add(InlineKeyboardButton("🔙 العودة للسوق", callback_data=f"market_tab:{user_id}:trending"))
    return markup

# ==========================================
# 4. المستمعات النصية الأساسية (Text Listeners)
# ==========================================

# تم استخدام Text(equals=[...]) للاستجابة المباشرة للنص بدون أي شرطة أو علامة
@dp.message_handler(Text(equals=["محفظتي", "المحفظة"], ignore_case=True))
async def listener_wallet(message: types.Message):
    user_id = message.from_user.id
    
    # جلب بيانات المستخدم من قاعدة البيانات
    data = await get_user_data(user_id)
    if not data: 
        return await message.answer("❌ <b>عذراً!</b> سجل حسابك أولاً بالضغط على /start", parse_mode="HTML")

    bank_bal = float(data.get('bank_balance', 0))
    wallet_bal = float(data.get('wallet', 0))
    debt = float(data.get('debt_balance', 0))
    
    # فحص الصفقات النشطة
    trades_res = supabase.table("active_trades").select("id").eq("user_id", user_id).eq("is_active", True).execute()
    active_count = len(trades_res.data) if trades_res.data else 0
    
    text = "🏦 | <b>مـركـز إدارة الأمـوال والأصول</b>\n"
    text += "━━━━━━━━━━━━━━━━━━\n"
    text += f"👤 الـمـسـتـخدم: <b>{message.from_user.first_name}</b>\n\n"
    text += f"💳 <b>رصـيد الـمحفظة:</b> <code>{wallet_bal:,.2f} $</code>\n"
    text += f"📈 <b>حـساب الـتداول:</b> <code>{bank_bal:,.2f} $</code>\n"
    text += "━━━━━━━━━━━━━━━━━━\n"
    text += f"📊 صـفقات مـفـتوحة حالياً: <b>{active_count}</b>\n"
    
    # فحص الديون لعرض التنبيه
    if debt > 0:
        text += f"⚠️ <b>الـديون الـمستحقة:</b> <code>{debt:,.2f} $</code>\n"
    else:
        text += "✅ <b>حالة الائتمان:</b> سليم\n"
    
    text += "━━━━━━━━━━━━━━━━━━\n"
    
    await message.answer(text, reply_markup=get_wallet_keyboard(user_id, debt), parse_mode="HTML")

@dp.message_handler(Text(equals=["تداول", "السوق", "التداول"], ignore_case=True))
async def listener_market(message: types.Message):
    user_id = message.from_user.id
    
    # جلب العملات من السوق (Binance Mode)
    res = supabase.table("crypto_market_simulation").select("*").order("volume_24h", desc=True).limit(5).execute()
    coins = res.data
    
    text = "📊 | <b>سـوق الـعـمـلات (Binance Mode)</b>\n"
    text += "━━━━━━━━━━━━━━━━━━\n"
    text += "🔥 <b>الأكثر رواجاً حالياً:</b>\n\n"
    
    markup = get_market_keyboard(user_id)
    
    if not coins:
        text += "⚠️ لا توجد بيانات في السوق حالياً."
    else:
        for c in coins:
            sym = c['symbol']
            price = float(c['current_price'])
            chg = float(c['change_24h'])
            icon = "🟢" if chg >= 0 else "🔴"
            text += f"{icon} <b>{sym}</b> : <code>{price:,.4f} $</code> ({chg:+.2f}%)\n"
            # إضافة أزرار العملات تحت الرسالة
            markup.add(InlineKeyboardButton(f"عرض {sym} 🪙", callback_data=f"coin_view:{user_id}:{sym}"))

    await message.answer(text, reply_markup=markup, parse_mode="HTML")

@dp.message_handler(Text(equals=["صفقاتي", "الصفقات"], ignore_case=True))
async def listener_trades(message: types.Message):
    user_id = message.from_user.id
    trades, text = await get_active_trades_report(user_id)
    
    if not trades:
        return await message.answer(text, reply_markup=get_market_keyboard(user_id), parse_mode="HTML")
    
    await message.answer(text, reply_markup=get_trades_keyboard(user_id, trades), parse_mode="HTML")
# ==========================================
# 5. دوال مساعدة للواجهات (UI Helpers)
# ==========================================

async def is_authorized(callback_query: types.CallbackQuery):
    """
    🛡️ الحارس الشخصي: يفكك الـ callback_data ويتأكد أن اللي ضغط الزر هو صاحبه.
    يعمل فقط على أزرار التداول ولا يتدخل في أزرار المسابقات.
    """
    data_parts = callback_query.data.split(':')
    # نفترض أن الآيدي دائماً في الخانة الثانية، مثال: setup_trade:123456:BTC
    if len(data_parts) > 1 and data_parts[1].isdigit():
        owner_id = int(data_parts[1])
        if callback_query.from_user.id != owner_id:
            await callback_query.answer("🚫 هذي ليست محفظتك! العب بعيد يا مبعسس 🤫", show_alert=True)
            return False
    return True

async def update_trade_ui(callback_query: types.CallbackQuery):
    """دالة مساعدة لتحديث شاشة إعداد الصفقة أثناء دوران الأزرار"""
    user_id = callback_query.from_user.id
    if user_id not in trade_sessions: return
    
    session = trade_sessions[user_id]
    sym = session['symbol']
    side = session['side']
    price = session['entry_price']
    bal = session['balance']
    lev = session['leverage']
    pct = session['margin_pct']
    
    margin_amount = bal * (pct / 100.0)
    total_position = margin_amount * lev
    quantity = total_position / price if price > 0 else 0
    liq_price = calculate_liquidation(price, lev, side)
    
    side_text = "🟢 شـراء (LONG) 🚀" if side == 'LONG' else "🔴 بـيـع (SHORT) 🩸"
    
    text = f"⚙️ | <b>إعـداد صـفـقـة: #{sym}</b>\n"
    text += f"الـنـوع: {side_text}\n"
    text += "━━━━━━━━━━━━━━━━━━\n"
    text += f"💵 سـعـر الـدخول: <code>{price:,.4f} $</code>\n"
    text += f"🏦 رصـيـدك الـمـتـاح: <code>{bal:,.2f} $</code>\n\n"
    text += f"⚖️ الـرافـعـة الـمـالـيـة: <b>{lev}x</b>\n"
    text += f"💼 الـمـبـلـغ الـمـسـتـخـدم: <b>{margin_amount:,.2f} $</b> ({pct}%)\n"
    text += f"🪙 حـجـم الـعـمـلات: <b>{quantity:,.6f} {sym}</b>\n"
    text += f"⏳ الـمـدة الـمـحـددة: <b>{DURATION_MAP[session['duration']][0]}</b>\n\n"
    text += f"⚠️ <b>سـعـر الـتـصـفـيـة الـمـتـوقـع:</b> <code>{liq_price:,.4f} $</code>\n"
    text += "━━━━━━━━━━━━━━━━━━\n"
    text += "<i>اضغط على الأزرار لتغيير النسب، ثم قم بالتأكيد.</i>"

    await bot.edit_message_text(text, callback_query.message.chat.id, callback_query.message.message_id, 
                                reply_markup=get_trade_setup_keyboard(user_id), parse_mode="HTML")

# ==========================================
# 6. معالجات الأزرار الأساسية (Secured Callbacks)
# ==========================================

@dp.callback_query_handler(Text(startswith='repay_loan:'))
async def process_repay_loan(callback_query: types.CallbackQuery):
    if not await is_authorized(callback_query): return
    
    user_id = callback_query.from_user.id
    user_data = await get_user_data(user_id)
    
    bank_bal = float(user_data['bank_balance'])
    debt = float(user_data['debt_balance'])
    
    if debt <= 0:
        return await callback_query.answer("✅ ليس لديك ديون لتسديدها!", show_alert=True)
        
    if bank_bal < debt:
        # تسديد جزئي إذا كان الرصيد أقل من الدين
        repay_amount = bank_bal
        new_debt = debt - repay_amount
        new_bank = 0
    else:
        # تسديد كامل
        repay_amount = debt
        new_debt = 0
        new_bank = bank_bal - repay_amount
        
    supabase.table("users_global_profile").update({
        "bank_balance": new_bank,
        "debt_balance": new_debt
    }).eq("user_id", user_id).execute()
    
    await callback_query.answer(f"✅ تم تسديد {repay_amount:,.2f}$ من ديونك.", show_alert=True)
    await listener_wallet(callback_query.message)
    
@dp.callback_query_handler(Text(startswith='market_tab:'))
async def callback_market_tabs(callback_query: types.CallbackQuery):
    if not await is_authorized(callback_query): return
    
    user_id = callback_query.from_user.id
    tab_type = callback_query.data.split(':')[2]
    
    # جلب العملات حسب الفلتر
    if tab_type == 'gainers':
        res = supabase.table("crypto_market_simulation").select("*").order("change_24h", desc=True).limit(5).execute()
        header = "📈 <b>الأكثر ربحاً:</b>"
    elif tab_type == 'losers':
        res = supabase.table("crypto_market_simulation").select("*").order("change_24h", desc=False).limit(5).execute()
        header = "📉 <b>الأكثر خسارة:</b>"
    else: # trending
        res = supabase.table("crypto_market_simulation").select("*").order("volume_24h", desc=True).limit(5).execute()
        header = "🔥 <b>الأكثر رواجاً:</b>"
        
    text = f"📊 | <b>سـوق الـعـمـلات (Binance Mode)</b>\n━━━━━━━━━━━━━━━━━━\n{header}\n\n"
    markup = get_market_keyboard(user_id)
    
    for c in res.data:
        sym = c['symbol']
        price = float(c['current_price'])
        chg = float(c['change_24h'])
        icon = "🟢" if chg >= 0 else "🔴"
        text += f"{icon} <b>{sym}</b> : <code>{price:,.4f} $</code> ({chg:+.2f}%)\n"
        markup.add(InlineKeyboardButton(f"عرض {sym} 🪙", callback_data=f"coin_view:{user_id}:{sym}"))

    await callback_query.message.edit_text(text, reply_markup=markup, parse_mode="HTML")

@dp.callback_query_handler(Text(startswith='wallet_view:'))
async def callback_wallet_view(callback_query: types.CallbackQuery):
    if not await is_authorized(callback_query): return
    # إعادة توجيه المستلم للدالة النصية لتوحيد الكود
    await listener_wallet(callback_query.message)

@dp.callback_query_handler(Text(startswith='active_trades_view:'))
async def callback_view_trades(callback_query: types.CallbackQuery):
    if not await is_authorized(callback_query): return
    
    user_id = callback_query.from_user.id
    trades, text = await get_active_trades_report(user_id)
    
    if not trades:
        return await callback_query.message.edit_text(text, reply_markup=get_market_keyboard(user_id), parse_mode="HTML")
        
    await callback_query.message.edit_text(text, reply_markup=get_trades_keyboard(user_id, trades), parse_mode="HTML")

@dp.callback_query_handler(Text(startswith='coin_view:'))
async def process_coin_view(callback_query: types.CallbackQuery):
    if not await is_authorized(callback_query): return
    
    user_id = callback_query.from_user.id
    symbol = callback_query.data.split(':')[2]
    res = supabase.table("crypto_market_simulation").select("*").eq("symbol", symbol).execute()
    
    if not res.data:
        return await callback_query.answer("⚠️ العملة غير موجودة!", show_alert=True)
        
    coin = res.data[0]
    price = float(coin['current_price'])
    ema50 = float(coin.get('ema_50', price))
    rsi = float(coin.get('rsi_val', 50))
    bb_upper = float(coin.get('bb_upper', price * 1.05))
    bb_lower = float(coin.get('bb_lower', price * 0.95))
    direction = coin.get('last_tick_direction', 'UP')
    
    ema_status = "السعر فوق الخط 🟢 صعود" if price > ema50 else "السعر تحت الخط 🔴 هبوط"
    
    if rsi >= 78: rsi_status = "تشبع شرائي ذروة 🔴 (احذر الهبوط)"
    elif rsi <= 22: rsi_status = "تشبع بيعي ذروة 🟢 (فرصة ارتداد)"
    else: rsi_status = "منطقة محايدة 🟡"
    
    text = f"🪙 | <b>عـمـلـة: #{symbol}</b>\n"
    text += f"💰 الـسـعـر الـحـالـي: <code>{price:,.4f} $</code>\n"
    text += f"📉 نـسـبـة 24س: {float(coin['change_24h']):+.2f}%\n"
    text += "━━━━━━━━━━━━━━━━━━\n"
    text += f"📊 <b>الـمـؤشـرات الـفـنـيـة (Live):</b>\n"
    text += f"• <b>EMA 50:</b> {ema50:,.4f} ({ema_status})\n"
    text += f"• <b>RSI (78/22):</b> {rsi:.1f} ({rsi_status})\n"
    text += f"• <b>Bollinger MID:</b> {float(coin.get('bb_middle', price)):,.4f}\n"
    text += f"   - المقاومة (أصفر): {bb_upper:,.4f}\n"
    text += f"   - الدعم (أصفر): {bb_lower:,.4f}\n\n"
    text += f"شكل الشمعة الحالية:\n{generate_candle_chart(direction)}\n"
    text += "━━━━━━━━━━━━━━━━━━\n"
    text += "اختر إجراء التداول الآن 👇:"

    await callback_query.message.edit_text(text, reply_markup=get_coin_keyboard(user_id, symbol), parse_mode="HTML")

# ==========================================
# 7. معالجات دورة الصفقة (Setup, Cycle, Confirm)
# ==========================================

@dp.callback_query_handler(Text(startswith='setup_trade:'))
async def process_setup_trade(callback_query: types.CallbackQuery):
    if not await is_authorized(callback_query): return
    
    user_id = callback_query.from_user.id
    _, _, symbol, side = callback_query.data.split(':')
    
    coin = supabase.table("crypto_market_simulation").select("current_price").eq("symbol", symbol).execute().data[0]
    price = float(coin['current_price'])
    balance = await get_user_bank_balance(user_id)
    
    trade_sessions[user_id] = {
        'symbol': symbol,
        'side': side,
        'entry_price': price,
        'leverage': 10,
        'margin_pct': 25,
        'duration': '4h',
        'balance': balance
    }
    
    await update_trade_ui(callback_query)

@dp.callback_query_handler(Text(startswith='trade_cycle:'))
async def process_trade_cycle(callback_query: types.CallbackQuery):
    if not await is_authorized(callback_query): return
    
    user_id = callback_query.from_user.id
    if user_id not in trade_sessions:
        return await callback_query.answer("⚠️ انتهت الجلسة، الرجاء فتح العملة مجدداً.", show_alert=True)
    
    action = callback_query.data.split(':')[2]
    session = trade_sessions[user_id]
    
    if action == 'leverage':
        idx = LEVERAGE_LEVELS.index(session['leverage'])
        session['leverage'] = LEVERAGE_LEVELS[(idx + 1) % len(LEVERAGE_LEVELS)]
    elif action == 'margin':
        idx = MARGIN_PCT_LEVELS.index(session['margin_pct'])
        session['margin_pct'] = MARGIN_PCT_LEVELS[(idx + 1) % len(MARGIN_PCT_LEVELS)]
    elif action == 'duration':
        idx = DURATION_KEYS.index(session['duration'])
        session['duration'] = DURATION_KEYS[(idx + 1) % len(DURATION_KEYS)]
        
    await update_trade_ui(callback_query)

@dp.callback_query_handler(Text(startswith='trade_confirm:'))
async def process_trade_confirm(callback_query: types.CallbackQuery):
    if not await is_authorized(callback_query): return
    
    user_id = callback_query.from_user.id
    if user_id not in trade_sessions:
        return await callback_query.answer("⚠️ حدث خطأ أو الجلسة انتهت.", show_alert=True)
        
    session = trade_sessions[user_id]
    margin_amount = session['balance'] * (session['margin_pct'] / 100.0)
    
    if margin_amount <= 0 or margin_amount > session['balance']:
        return await callback_query.answer("❌ رصيدك غير كافٍ لهذه العملية!", show_alert=True)
        
    coin_data = supabase.table("crypto_market_simulation").select("current_price").eq("symbol", session['symbol']).execute()
    current_price = float(coin_data.data[0]['current_price'])
    
    quantity = (margin_amount * session['leverage']) / current_price
    liq_price = calculate_liquidation(current_price, session['leverage'], session['side'])
    expiry = datetime.now() + DURATION_MAP[session['duration']][1]
    
    try:
        new_balance = session['balance'] - margin_amount
        supabase.table("users_global_profile").update({"bank_balance": new_balance}).eq("user_id", user_id).execute()
        
        supabase.table("active_trades").insert({
            "user_id": user_id,
            "symbol": session['symbol'],
            "side": session['side'],
            "entry_price": current_price,
            "leverage": session['leverage'],
            "margin": margin_amount,
            "quantity": quantity,
            "liquidation_price": liq_price,
            "expiry_time": expiry.isoformat(),
            "is_active": True
        }).execute()
        
        del trade_sessions[user_id]
        
        text = "✅ <b>تـم فـتـح الـصـفـقـة بـنـجـاح!</b> 🚀\n\n"
        text += f"العملة: #{session['symbol']}\n"
        text += f"النوع: {session['side']}\n"
        text += f"سعر الدخول: {current_price:,.4f} $\n"
        text += f"المبلغ المحجوز: {margin_amount:,.2f} $\n"
        text += f"رصيدك المتبقي: {new_balance:,.2f} $"
        
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("📋 عرض صفقاتي", callback_data=f"active_trades_view:{user_id}"))
        markup.add(InlineKeyboardButton("🔙 العودة للسوق", callback_data=f"market_tab:{user_id}:trending"))
        
        await callback_query.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
          
    except Exception as e:
        logging.error(f"Trade Insert Error: {e}")
        await callback_query.answer("❌ حدث خطأ داخلي أثناء تنفيذ الصفقة.", show_alert=True)

# ==========================================
# 8. إدارة الصفقات المفتوحة (DCA & Close)
# ==========================================

@dp.callback_query_handler(Text(startswith='dca_trade:'))
async def process_dca_trade(callback_query: types.CallbackQuery):
    if not await is_authorized(callback_query): return
    
    data = callback_query.data.split(':')
    user_id = int(data[1])
    trade_id = data[2]

    res = supabase.table("active_trades").select("*").eq("id", trade_id).execute()
    if not res.data: return await callback_query.answer("⚠️ الصفقة غير موجودة أو مغلقة.")
    
    trade = res.data[0]
    symbol = trade['symbol']
    old_margin = float(trade['margin'])
    old_entry = float(trade['entry_price'])
    side = trade['side']
    leverage = int(trade['leverage'])

    coin_res = supabase.table("crypto_market_simulation").select("current_price").eq("symbol", symbol).execute()
    current_price = float(coin_res.data[0]['current_price'])

    bank_bal = await get_user_bank_balance(user_id)
    dca_amount = old_margin 
    
    if bank_bal < dca_amount:
        return await callback_query.answer(f"❌ رصيدك لا يكفي للتعزيز! تحتاج {dca_amount:,.2f} $", show_alert=True)

    new_margin = old_margin + dca_amount
    old_units = (old_margin * leverage) / old_entry
    new_units_added = (dca_amount * leverage) / current_price
    total_units = old_units + new_units_added
    new_entry_price = (new_margin * leverage) / total_units
    new_liquidation = calculate_liquidation(new_entry_price, leverage, side)

    new_bank_bal = bank_bal - dca_amount
    supabase.table("users_global_profile").update({"bank_balance": new_bank_bal}).eq("user_id", user_id).execute()
    
    supabase.table("active_trades").update({
        "margin": new_margin,
        "entry_price": new_entry_price,
        "liquidation_price": new_liquidation
    }).eq("id", trade_id).execute()

    await callback_query.answer(f"🚀 تم تعزيز الصفقة بنجاح!\nالمتوسط الجديد: {new_entry_price:,.4f}", show_alert=True)
    await callback_view_trades(callback_query)

@dp.callback_query_handler(Text(startswith='close_trade:'))
async def handle_manual_close_request(callback_query: types.CallbackQuery):
    if not await is_authorized(callback_query): return
    
    data = callback_query.data.split(':')
    user_id = int(data[1])
    trade_id = data[2]
    
    res = supabase.table("active_trades").select("symbol").eq("id", trade_id).execute()
    if not res.data: return await callback_query.answer("⚠️ الصفقة مغلقة.")
    
    symbol = res.data[0]['symbol']
    coin_res = supabase.table("crypto_market_simulation").select("current_price").eq("symbol", symbol).execute()
    current_price = float(coin_res.data[0]['current_price'])
    
    success, pnl = await close_trade_manually(trade_id, current_price)
    
    if success:
        await callback_query.answer(f"✅ تم الإغلاق! الربح/الخسارة: {pnl:+.2f} $", show_alert=True)
        await callback_view_trades(callback_query) 
    else:
        await callback_query.answer("❌ فشل إغلاق الصفقة.")

# ==========================================
# 9. إدارة الأموال (Transfers & Loans)
# ==========================================

@dp.callback_query_handler(Text(startswith='transfer_flow:'))
async def transfer_init(callback_query: types.CallbackQuery):
    if not await is_authorized(callback_query): return
    user_id = callback_query.from_user.id
    direction = callback_query.data.split(':')[2]
    
    prompt = "📥 <b>إيداع للتداول</b>\nأدخل المبلغ المراد تحويله من محفظتك إلى التداول:" if direction == "to_bank" else \
             "📤 <b>سحب للمحفظة</b>\nأدخل المبلغ المراد سحبه (سيتم فحص الديون والصفقات):"
    
    await bot.send_message(callback_query.message.chat.id, prompt, reply_markup=types.ForceReply(selective=True), parse_mode="HTML")
    trade_sessions[f"wait_trans_{user_id}"] = direction
    await callback_query.answer()

@dp.message_handler(lambda m: m.reply_to_message and ("إيداع للتداول" in m.reply_to_message.text or "سحب للمحفظة" in m.reply_to_message.text))
async def transfer_processor(message: types.Message):
    user_id = message.from_user.id
    key = f"wait_trans_{user_id}"
    if key not in trade_sessions: return
    
    direction = trade_sessions[key]
    try:
        amount = float(message.text)
        if amount <= 0: raise ValueError
    except:
        return await message.reply("❌ يرجى إدخال رقم صحيح.")

    is_safe, msg = await check_financial_health(user_id, amount, "WITHDRAW" if direction == "to_wallet" else "DEPOSIT")
    if not is_safe: return await message.reply(msg)

    data = await get_user_data(user_id)
    if direction == "to_bank":
        if amount > float(data['wallet']): return await message.reply("❌ رصيد المحفظة غير كافٍ.")
        supabase.table("users_global_profile").update({"wallet": float(data['wallet'])-amount, "bank_balance": float(data['bank_balance'])+amount}).eq("user_id", user_id).execute()
    else:
        supabase.table("users_global_profile").update({"bank_balance": float(data['bank_balance'])-amount, "wallet": float(data['wallet'])+amount}).eq("user_id", user_id).execute()

    del trade_sessions[key]
    await message.reply(f"✅ تم تحويل <b>{amount:,.2f} $</b> بنجاح!", parse_mode="HTML")

@dp.callback_query_handler(Text(startswith='loan_menu:'))
async def loan_menu(callback_query: types.CallbackQuery):
    if not await is_authorized(callback_query): return
    user_id = callback_query.from_user.id
    
    is_safe, msg = await check_financial_health(user_id, 0, "BORROW")
    if not is_safe: return await callback_query.answer(msg, show_alert=True)
    
    data = await get_user_data(user_id)
    max_loan = float(data['bank_balance']) * 0.5 

    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton(f"💰 اقتراض {max_loan:,.2f} $ (قرض ذكي)", callback_data=f"exec_loan:{user_id}:{max_loan}"))
    markup.add(InlineKeyboardButton("🔙 عودة للمحفظة", callback_data=f"wallet_view:{user_id}"))
    
    text = f"🏦 | <b>مـركـز الائـتـمـان والـقـروض</b>\n━━━━━━━━━━━━━━━━━━\n"
    text += "نظام القرض الذكي يمنحك سيولة بضمان رصيدك الحالي.\n\n"
    text += f"💵 الـمبلغ الـمتاح لك حالياً: <b>{max_loan:,.2f} $</b>\n━━━━━━━━━━━━━━━━━━\n"
    text += "<i>* تنبيه: لا يمكن السحب للمحفظة قبل سداد كامل القرض.</i>"

    await callback_query.message.edit_text(text, reply_markup=markup, parse_mode="HTML")

@dp.callback_query_handler(Text(startswith='exec_loan:'))
async def exec_loan_handler(callback_query: types.CallbackQuery):
    if not await is_authorized(callback_query): return
    
    data = callback_query.data.split(':')
    user_id = int(data[1])
    loan_amount = float(data[2])
    
    user_data = await get_user_data(user_id)
    if not user_data: return await callback_query.answer("❌ خطأ: ملف اللاعب مفقود.")

    new_bank = float(user_data.get('bank_balance', 0)) + loan_amount
    new_debt = float(user_data.get('debt_balance', 0)) + loan_amount

    try:
        supabase.table("users_global_profile").update({
            "bank_balance": new_bank,
            "debt_balance": new_debt,
            "last_loan_date": datetime.now().isoformat() 
        }).eq("user_id", user_id).execute()
        
        await callback_query.answer(f"✅ تم إيداع {loan_amount:,.2f} $ كقرض بنجاح.", show_alert=True)
        await listener_wallet(callback_query.message) # تحديث الواجهة
        
    except Exception as e:
        logging.error(f"Loan Error: {e}")
        await callback_query.answer("❌ فشل التنفيذ، حاول لاحقاً.", show_alert=True)
        
        
        # ==========================================
# 5. نهاية الملف: نظام الإنعاش الأبدي 24/7 (النبض الذاتي) ⚡
# ==========================================
from aiohttp import web
import os
import asyncio

async def handle_ping(request):
    return web.Response(text="🚀 البوت يعمل بنبض مستقر")

async def handle_telegram_login(request):
    return web.Response(text="✅ تم استقبال البيانات")


async def handle_ping(request):
    # إضافة هيدر يخبر ريندر أن الاتصال يجب أن يبقى حياً
    return web.Response(
        text="Alive ⚡", 
        headers={"Connection": "keep-alive"}
    )

# 2. 🪄 الخدعة السحرية: النبض الذاتي (البوت يوقظ ن

async def self_resuscitation():
    render_url = os.getenv("RENDER_EXTERNAL_URL") 
    if not render_url: return

    while True:
        try:
            # إضافة رقم عشوائي في نهاية الرابط لكسر "التخزين المؤقت" لـ ريندر
            # سيصبح الرابط مثل: https://bot.onrender.com/?v=12345
            rand_ping = f"{render_url}?v={random.randint(1, 99999)}"
            
            async with aiohttp.ClientSession() as session:
                async with session.get(rand_ping, timeout=10) as response:
                    logging.info(f"💉 [نبضة حية]: {response.status} | الرابط: {rand_ping}")
        except Exception as e:
            logging.error(f"⚠️ [فشل النبض]: {e}")
        
        # اجعلها كل 4 دقائق (240 ثانية) - كن "مزعجاً" لسيرفر ريندر لكي لا ينام
        await asyncio.sleep(240)
        
async def main_startup():
    # أ) إعداد سيرفر الويب
    app = web.Application()
    app.router.add_get('/', handle_ping)
    app.router.add_get('/login', handle_telegram_login)
    
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logging.info(f"🌐 Server started on port {port}")

    # ب) تشغيل محركات التداول في الخلفية
    logging.info("⏳ جاري تشغيل محركات السوق والرادار...")
    asyncio.create_task(market_engine())
    asyncio.create_task(trade_reaper())

    # ج) تشغيل البوت (التصحيح هنا)
    try:
        logging.info("🚀 جاري إقلاع محرك التليجرام...")
        
        # في الإصدارات الحديثة، نستخدم drop_pending_updates=True بدلاً من skip_updates
        # ونقوم بحذف reset_webhook إذا كنت تستخدم Polling عادي
        await dp.start_polling(bot, drop_pending_updates=True)
        
    except Exception as e:
        logging.error(f"❌ خطأ في تشغيل البوت: {e}")
    finally:
        await bot.session.close()
