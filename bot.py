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
LEVERAGE_LEVELS = [1, 2, 5, 10, 20, 45]
MARGIN_PCT_LEVELS = [2, 5, 10, 25, 50, 75, 100]
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
            
        await asyncio.sleep(600) # تحديث كل دقيقة

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
            
        await asyncio.sleep(600) # فحص كل 30 ثانية
                
# ==========================================
# 1. الدوال الحسابية (Math Core)
# ==========================================
async def get_user_bank_balance(user_id):
    """جلب رصيد المستخدم من قاعدة البيانات"""
    try:
        res = supabase.table("users_global_profile").select("bank_balance").eq("user_id", user_id).execute()
        if res.data:
            return float(res.data[0]['bank_balance'])
        return 0.0
    except:
        return 0.0


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
    """حساب الأرباح والخسائر (PnL) وعرض تفاصيل الصفقة بالتنسيق الجديد"""
    res = supabase.table("active_trades").select("*").eq("user_id", int(user_id)).eq("is_active", True).execute()
    trades = res.data
    
    if not trades:
        return None, "📋 <b>لا توجد صفقات مفتوحة حالياً.</b>"

    report_text = "📋 | <b>قـائمة صـفـقاتك الـمفتوحة</b>\n"
    report_text += "━━━━━━━━━━━━━━━━━━\n"

    for trade in trades:
        symbol = trade['symbol']
        side = "🟢 LONG" if trade['side'] == 'LONG' else "🔴 SHORT"
        
        entry = int(trade['entry_price'])
        lev = int(trade['leverage'])
        margin = int(trade['margin'])
        quantity = int(trade.get('quantity', 0))
        liq_price = int(trade.get('liquidation_price', 0))
        
        # معالجة حقول قاعدة البيانات int8 بأمان
        tp = trade.get('take_profit')
        sl = trade.get('stop_loss')
        
        coin_res = supabase.table("crypto_market_simulation").select("current_price").eq("symbol", symbol).execute()
        current_price = int(coin_res.data[0]['current_price']) if coin_res.data else entry

        if entry > 0:
            pnl_pct = (current_price - entry) / entry if trade['side'] == 'LONG' else (entry - current_price) / entry
        else:
            pnl_pct = 0.0
            
        pnl_amount = int(margin * pnl_pct * lev)
        pnl_emoji = "💰" if pnl_amount >= 0 else "📉"

        # --- بناء القالب الجديد (كل عنصر في سطر) ---
        report_text += f"<b>#{symbol} | {side} {lev}x</b>\n"
        report_text += f"• الـكمية: <code>{quantity:,}</code>\n"
        report_text += f"• سـعر الـدخول: <code>{entry:,}</code>\n"
        report_text += f"• الـسعر الحالي: <code>{current_price:,}</code>\n"
        report_text += f"{pnl_emoji} الـربح/الخسارة: <b>{pnl_amount:+,} $</b>\n"
        
        # إظهار الأهداف فقط إذا كانت موجودة (تحويلها إلى int لتنظيف الفواصل)
        if tp is not None: report_text += f"• هدف الربح (TP): <code>{int(tp):,}</code> 🎯\n"
        if sl is not None: report_text += f"• وقـف الخسارة (SL): <code>{int(sl):,}</code> 🛑\n"
        
        report_text += f"• سـعر الـتصفية: <code>{liq_price:,}</code> ⚠️\n"
        report_text += "━━━━━━━━━━━━━━━━━━\n"
        
    return trades, report_text
    

def get_trade_settings_view(trade, current_price, expand_section=None):
    symbol = trade['symbol']
    entry = int(trade['entry_price'])
    liq = int(trade['liquidation_price'])
    t_id = str(trade['trade_id'])
    u_id = str(trade['user_id']) # 🛡️ الآيدي للحماية
    
    # 📌 الرأس الثابت للشاشة
    text = f"⚙️ <b>لوحة تحكم المركز: #{symbol}</b>\n"
    text += f"━━━━━━━━━━━━━━━━━━\n"
    text += f"• سعر الدخول: <code>{entry:,}</code> | السعر الحالي: <code>{current_price:,}</code>\n"
    text += f"• التصفية: <pre>{liq:,}</pre> ⚠️\n"
    text += f"━━━━━━━━━━━━━━━━━━\n"

    markup = InlineKeyboardMarkup(row_width=1)
    
    # 🌿 القائمة الرئيسية (الآباء)
    if not expand_section:
        markup.add(
            # exp = expand | cl = close | dca = dca | risk = risk
            InlineKeyboardButton("✂️ إغلاق جزئي لصفقة", callback_data=f"exp_cl_{u_id}_{t_id}"),
            InlineKeyboardButton("🚀 تعزيز الصفقة (DCA)", callback_data=f"exp_dca_{u_id}_{t_id}"),
            InlineKeyboardButton("🎯 أهداف الربح والخسارة", callback_data=f"exp_risk_{u_id}_{t_id}"),
            InlineKeyboardButton("🔙 العودة للقائمة", callback_data=f"active_trades_view_{u_id}")
        )
    
    # 🌿 قائمة أبناء (الإغلاق الجزئي)
    elif expand_section == "cl":
        text += "<b>💡 اختر نسبة الإغلاق من حجم العقد:</b>"
        markup.row(
            InlineKeyboardButton("5%", callback_data=f"pcl_5_{u_id}_{t_id}"),
            InlineKeyboardButton("10%", callback_data=f"pcl_10_{u_id}_{t_id}"),
            InlineKeyboardButton("25%", callback_data=f"pcl_25_{u_id}_{t_id}"),
            InlineKeyboardButton("50%", callback_data=f"pcl_50_{u_id}_{t_id}"),
            InlineKeyboardButton("75%", callback_data=f"pcl_75_{u_id}_{t_id}")
        )
        markup.add(InlineKeyboardButton("🛑 إغلاق 100% (تأكيد)", callback_data=f"conf_cl_100_{u_id}_{t_id}"))
        markup.add(InlineKeyboardButton("🔙 رجوع", callback_data=f"back_ts_{u_id}_{t_id}")) # ts = Trade Settings

    # 🌿 قائمة أبناء (التعزيز DCA)
    elif expand_section == "dca":
        text += "<b>🚀 اختر نسبة التعزيز من المحفظة:</b>"
        markup.row(
            InlineKeyboardButton("5%", callback_data=f"pcl_5_{u_id}_{t_id}"),
            InlineKeyboardButton("10%", callback_data=f"pcl_10_{u_id}_{t_id}"),
            InlineKeyboardButton("25%", callback_data=f"pcl_25_{u_id}_{t_id}"),
            InlineKeyboardButton("50%", callback_data=f"pcl_50_{u_id}_{t_id}"),
            InlineKeyboardButton("75%", callback_data=f"dca_75_{u_id}_{t_id}")
        )
        markup.add(InlineKeyboardButton("🔥 تعزيز 100% (تأكيد)", callback_data=f"conf_dca_100_{u_id}_{t_id}"))
        markup.add(InlineKeyboardButton("🔙 رجوع", callback_data=f"back_ts_{u_id}_{t_id}"))

    # 🌿 قائمة أبناء (إدارة المخاطر)
    # 🌿 قائمة أبناء (إدارة المخاطر الاحترافية - نظام بينانس المطور)
    elif expand_section == "risk":
        side = trade['side']
        entry = float(trade['entry_price'])
        liq = float(trade['liquidation_price'])
        lev = int(trade['leverage'])
        qty = float(trade.get('quantity', 0)) # الكمية (المقدار)
        c_price = float(current_price)
        
        # 💰 حساب الرصيد الصافي (Equity) = الرصيد + الأرباح/الخسائر غير المحققة
        # ملاحظة: يجب توفير 'net_balance' من قاعدة البيانات أو تمريرها كمتغير
        net_balance = float(trade.get('net_balance', 100)) # افتراضي 100$ كمثال
        
        text += f"\n<b>⚙️ نظام إدارة المحفظة (الصافي: {net_balance}$):</b>\n"
        text += f"• الرافعة: {lev}x | الدخول: {entry:,}\n"

        # 🧠 دالة حساب سعر الهدف بناءً على خسارة/ربح مبلغ محدد من الرصيد
        def get_price_by_pnl(amount_to_lose_or_gain, is_profit=False):
            # المعادلة: التغير في السعر = المبلغ / الكمية
            price_change = amount_to_lose_or_gain / qty if qty > 0 else 0
            if side == "LONG":
                return entry + price_change if is_profit else entry - price_change
            else:
                return entry - price_change if is_profit else entry + price_change

        # --- 🛑 نظام وقف الخسارة الديناميكي (SL1 - SL5) ---
        text += "\n<b>🛑 مستويات وقف الخسارة (تأمين الحساب):</b>"
        sl_buttons = []
        
        # الحالة 1: السعر رابح (تأمين الصفقة تلقائياً)
        is_in_profit = (side == "LONG" and c_price > entry) or (side == "SHORT" and c_price < entry)
        
        if is_in_profit:
            # توليد أزرار تأمين (Breakeven & Trailing)
            # SL1: نقطة الدخول، SL2: ربح بسيط، SL3-5: ملاحقة السعر
            targets = [
                (entry, "دخول 🛡️"),
                (calc_price(entry, 0.10, True), "تأمين +10%"), # ربح 10% ROE
                (calc_price(c_price, 0.05, False), "ملاحقة -5%"), # خلف الحالي بـ 5%
                (calc_price(c_price, 0.15, False), "ملاحقة -15%"),
                (calc_price(c_price, 0.25, False), "ملاحقة -25%")
            ]
        else:
            # الحالة 2: السعر خاسر أو قريب (تقسيم الرصيد الصافي على 5 مستويات)
            # كل مستوى يمثل خسارة 20% من رصيدك المخصص للصفقة
            loss_step = net_balance * 0.20 
            targets = []
            for i in range(1, 6):
                p = get_price_by_pnl(loss_step * i, is_profit=False)
                targets.append((p, f"SL{i} -{20*i}%"))

        # توزيع أزرار SL
        sl_row1, sl_row2, sl_row3 = [], [], []
        for i, (opt, label) in enumerate(targets):
            opt_val = float(opt)
            # حماية من التصفية
            if (side == "LONG" and opt_val > liq) or (side == "SHORT" and opt_val < liq) or is_in_profit:
                formatted_opt = int(opt_val) if opt_val > 10 else round(opt_val, 4)
                btn = InlineKeyboardButton(f"{label} ({formatted_opt})", callback_data=f"pre_risk_sl_{u_id}_{t_id}_{formatted_opt}")
                if len(sl_row1) < 2: sl_row1.append(btn)
                elif len(sl_row2) < 2: sl_row2.append(btn)
                else: sl_row3.append(btn)
        
        markup.row(*sl_row1) if sl_row1 else None
        markup.row(*sl_row2) if sl_row2 else None
        markup.row(*sl_row3) if sl_row3 else None

        # --- 💰 نظام جني الأرباح (6 أهداف: 3 متوسطة + 3 بعيدة) ---
        tp_levels = [
            (0.50, "M1 +50%"), (1.00, "M2 +100%"), (2.00, "M3 +200%"), # متوسطة
            (5.00, "L1 +500%"), (10.00, "L2 +1000%"), (20.00, "L3 +2000%") # بعيدة
        ]

        text += "\n\n<b>💰 أهداف جني الأرباح (6 مراحل):</b>"
        tp_row1, tp_row2, tp_row3 = [], [], []
        for i, (roe, label) in enumerate(tp_levels):
            # نستخدم دالة الـ ROE للأهداف لأنها الأكثر دقة في العقود
            opt = calc_price(entry, roe, True)
            formatted_opt = int(opt) if opt > 10 else round(opt, 4)
            btn = InlineKeyboardButton(f"{label} ({formatted_opt})", callback_data=f"pre_risk_tp_{u_id}_{t_id}_{formatted_opt}")
            if i < 2: tp_row1.append(btn)
            elif i < 4: tp_row2.append(btn)
            else: tp_row3.append(btn)
            
        markup.row(*tp_row1)
        markup.row(*tp_row2)
        markup.row(*tp_row3)

        markup.add(InlineKeyboardButton("🔙 رجوع للإعدادات", callback_data=f"back_ts_{u_id}_{t_id}"))

    return text, markup

# --- دالة مساعدة للحساب بناءً على الـ ROE ---
def calc_price(entry, roe_pct, is_tp, side, lev):
    move_pct = roe_pct / lev
    if side == "LONG":
        return entry * (1 + move_pct) if is_tp else entry * (1 - move_pct)
    else:
        return entry * (1 - move_pct) if is_tp else entry * (1 + move_pct)
        

async def close_trade_manually(trade_id, current_price):
    """إغلاق الصفقة وتصفية الحساب وإرجاع الرصيد للبنك"""
    # ⚠️ تصحيح مهم: العمود في جدولك اسمه trade_id وليس id
    res = supabase.table("active_trades").select("*").eq("trade_id", str(trade_id)).execute()
    if not res.data: 
        return False, "⚠️ الصفقة غير موجودة أو تم إغلاقها مسبقاً."
    
    trade = res.data[0]
    user_id = int(trade['user_id'])
    entry = int(trade['entry_price'])
    margin = int(trade['margin'])
    lev = int(trade['leverage'])
    side = trade['side']
    current_price = int(current_price) 
    
    # حساب النتيجة النهائية
    if entry > 0:
        pnl_pct = (current_price - entry) / entry if side == 'LONG' else (entry - current_price) / entry
    else:
        pnl_pct = 0.0
        
    pnl_amount = int(margin * pnl_pct * lev) # تحويل النتيجة لـ int
    total_return = margin + pnl_amount 
    
    # حماية: لا يمكن للرصيد العائد أن يكون بالسالب (أقصى خسارة هي الهامش)
    if total_return < 0: 
        total_return = 0 
    
    # تحديث رصيد البنك
    user_res = supabase.table("users_global_profile").select("bank_balance").eq("user_id", user_id).execute()
    if user_res.data:
        current_bank = int(user_res.data[0]['bank_balance'])
        new_bank = current_bank + total_return
        supabase.table("users_global_profile").update({"bank_balance": new_bank}).eq("user_id", user_id).execute()
    
    # ⚠️ تم إزالة close_price و pnl و closed_at من التحديث، لأنها غير موجودة في هيكل الـ SQL الذي أعطيتني إياه للجدول!
    # إذا أردت حفظها مستقبلاً، يجب إضافة هذه الأعمدة لجدول active_trades.
    supabase.table("active_trades").update({
        "is_active": False
    }).eq("trade_id", str(trade_id)).execute()
    
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
async def is_authorized(callback_query: types.CallbackQuery):
    """🛡️ الحارس الشخصي للتأكد من ملكية الأزرار"""
    data_parts = callback_query.data.split(':')
    if len(data_parts) > 1 and data_parts[1].isdigit():
        owner_id = int(data_parts[1])
        if callback_query.from_user.id != owner_id:
            await callback_query.answer("🚫 هذي ليست محفظتك! العب بعيد يا مبعسس 🤫", show_alert=True)
            return False
    return True

# ==========================================
# 3. قوالب واجهات المستخدم
# ==========================================
def get_coin_keyboard(user_id, symbol):
    markup = InlineKeyboardMarkup(row_width=2)
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
    markup.row(
        InlineKeyboardButton(f"⚖️ الرافعة: {session['leverage']}x", callback_data=f"trade_cycle:{user_id}:leverage"),
        InlineKeyboardButton(f"💼 النسبة: {session['margin_pct']}%", callback_data=f"trade_cycle:{user_id}:margin")
    )
    markup.add(InlineKeyboardButton(f"⏳ المدة: {DURATION_MAP[session['duration']][0]}", callback_data=f"trade_cycle:{user_id}:duration"))
    
    confirm_text = "🚀 تأكيد الشراء (LONG)" if side == 'LONG' else "🩸 تأكيد البيع (SHORT)"
    markup.add(InlineKeyboardButton(confirm_text, callback_data=f"trade_confirm:{user_id}:{sym}"))
    markup.add(InlineKeyboardButton("❌ إلغاء", callback_data=f"coin_view:{user_id}:{sym}"))
    return markup

async def update_trade_ui(callback_query: types.CallbackQuery):
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
    text += f"💵 سـعـر الـدخول: <code>{price:,.2f} $</code>\n"
    text += f"🏦 رصـيـدك الـمـتـاح: <code>{bal:,.2f} $</code>\n\n"
    text += f"⚖️ الـرافـعـة الـمـالـيـة: <b>{lev}x</b>\n"
    text += f"💼 الـمـبـلـغ الـمـسـتـخـدم: <b>{margin_amount:,.2f} $</b> ({pct}%)\n"
    text += f"🪙 حـجـم الـعـمـلات: <b>{quantity:,.2f} {sym}</b>\n"
    text += f"⏳ الـمـدة الـمـحـددة: <b>{DURATION_MAP[session['duration']][0]}</b>\n\n"
    text += f"⚠️ <b>سـعـر الـتـصـفـيـة الـمـتـوقـع:</b> <code>{liq_price:,.2f} $</code>\n"
    text += "━━━━━━━━━━━━━━━━━━\n"
    text += "<i>اضغط على الأزرار لتغيير النسب، ثم قم بالتأكيد.</i>"

    await bot.edit_message_text(text, callback_query.message.chat.id, callback_query.message.message_id, 
                                reply_markup=get_trade_setup_keyboard(user_id), parse_mode="HTML")


def get_wallet_keyboard(user_id, debt):
    markup = InlineKeyboardMarkup(row_width=2)
    
    # صف الإيداع والسحب
    markup.row(
        InlineKeyboardButton("📥 إيداع للتداول", callback_data=f"transfer_flow:{user_id}:to_bank"),
        InlineKeyboardButton("📤 سحب للمحفظة", callback_data=f"transfer_flow:{user_id}:to_wallet")
    )
    
    # زر القرض أو التسديد
    if debt > 0:
        # إذا كان عليه دين، يظهر زر التسديد باللون الأحمر (إيموجي)
        markup.add(InlineKeyboardButton("🔴 تسديد القرض المستحق", callback_data=f"repay_loan:{user_id}"))
    else:
        # إذا كان سليم، يظهر زر طلب القرض
        markup.add(InlineKeyboardButton("💰 طلب قرض سريع", callback_data=f"loan_menu:{user_id}"))
        
    # صف السوق والصفقات
    markup.row(
        InlineKeyboardButton("📋 صفقاتي", callback_data=f"active_trades_view:{user_id}"),
        InlineKeyboardButton("🛒 السوق", callback_data=f"market_tab:{user_id}:trending")
    )
    return markup

def get_trades_keyboard(user_id, trades):
    markup = InlineKeyboardMarkup(row_width=1) 
    for trade in trades:
        t_id_str = str(trade.get('trade_id'))
        symbol = trade.get('symbol', 'COIN')
        
        # أزرار الصفقة
        # زر الإغلاق الآن يرسل "pre_close" بدلاً من "close_trade" مباشرة
        markup.row(
            InlineKeyboardButton(f"⚙️ إعدادات {symbol}", callback_data=f"manage_trade:{t_id_str}"),
            InlineKeyboardButton(f"❌ إغلاق الصفقة", callback_data=f"conf_cl_100_{user_id}_{t_id_str}")
       )        
        
    markup.add(InlineKeyboardButton("🔙 العودة للسوق", callback_data=f"market_tab:{user_id}:trending"))
    return markup
    
class BankTransfer(StatesGroup):
    waiting_for_amount = State()      # انتظار مبلغ التحويل/الإيداع
    waiting_for_account = State()     # انتظار رقم الحساب (في حال التحويل لشخص)
# ==========================================
# 4. مستمعات المحفظة (متوافق مع Trade_ID)
# ==========================================
@dp.message_handler(Text(equals=["محفظتي", "المحفظة"], ignore_case=True), state="*")
async def message_wallet_view(message: types.Message):
    await process_wallet_logic(message.from_user.id, message.from_user.first_name, message=message)

async def process_wallet_logic(user_id, first_name, message=None, callback=None):
    try:
        # 1. جلب بيانات المستخدم
        res = supabase.table("users_global_profile").select("*").eq("user_id", user_id).execute()
        data = res.data[0] if res.data else None

        if not data:
            error_msg = "❌ لم يتم العثور على حسابك. ارسل /start للتسجيل."
            if message: await message.answer(error_msg)
            else: await callback.answer(error_msg, show_alert=True)
            return

        # 2. استخراج القيم المالية (نظام bigint)
        bank_bal = int(data.get('bank_balance', 0))
        wallet_bal = int(data.get('wallet', 0))
        debt = int(data.get('debt_balance', 0))
        rank = data.get('trading_rank', 'Beginner')
        flag = data.get('country_flag', '🇾🇪')

        # 3. تحليل الصفقات النشطة وحساب PnL المجمع
        trades_res = supabase.table("active_trades").select("*").eq("user_id", user_id).eq("is_active", True).execute()
        trades = trades_res.data if trades_res.data else []
        
        long_count = 0
        short_count = 0
        total_margin = 0
        total_pnl_amount = 0

        for trade in trades:
            # عد الصفقات حسب النوع
            if trade['side'] == 'LONG': long_count += 1
            else: short_count += 1
            
            # جلب البيانات للحساب
            symbol = trade['symbol']
            entry = int(trade['entry_price'])
            margin = int(trade['margin'])
            lev = int(trade['leverage'])
            total_margin += margin
            
            # جلب السعر الحالي
            coin_res = supabase.table("crypto_market_simulation").select("current_price").eq("symbol", symbol).execute()
            if coin_res.data:
                current_price = int(coin_res.data[0]['current_price'])
                if entry > 0:
                    # حساب الربح/الخسارة لهذه الصفقة
                    pnl_pct = (current_price - entry) / entry if trade['side'] == 'LONG' else (entry - current_price) / entry
                    total_pnl_amount += int(margin * pnl_pct * lev)

        # حساب النسبة المجمعة (الربح الإجمالي ÷ إجمالي الهوامش المستخدمة)
        avg_pnl_pct = (total_pnl_amount / total_margin * 100) if total_margin > 0 else 0
        pnl_color = "🟢" if total_pnl_amount >= 0 else "🔴"

        # 4. تنسيق الرسالة (التنسيق المطلوب)
        text = (
            f"🏦 | <b>مـركـز إدارة الأمـوال والأصول</b>\n"
            f"   ━━━━━━━━━━━━━━━━━━\n"
            f"👤 الـمـسـتـخدم: <b>{first_name}</b> {flag}\n"
            f"🏅 الخبرة: <b>{rank}</b>\n\n"
            f"💳 <b>رصـيد الـمحفظة:</b> <code>{wallet_bal:,} $</code>\n"
            f"📈 <b>حـساب الـتداول:</b> <code>{bank_bal:,} $</code>\n"
            f"   ━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>إحصائيات المـراكز:</b>\n"
            f"🟢 عدد صفقات الشراء: <b>{long_count}</b>\n"
            f"🔴 عدد صفقات البيع: <b>{short_count}</b>\n"
            f"   ━━━━━━━━━━━━━━━━━━\n"
            f"{pnl_color} <b>إجمالي الربح/الخسارة:</b> <b>{total_pnl_amount:+,} $</b>\n"
            f"📈 <b>نسبة العائد المجمع:</b> <b>{avg_pnl_pct:+.2f}%</b>\n"
            f"   ━━━━━━━━━━━━━━━━━━\n"
        )

        if debt > 0:
            text += f"⚠️ <b>الـديون الـمستحقة:</b> <code>{debt:,} $</code>\n"
        else:
            text += "✅ <b>حالة الائتمان:</b> سليم (لا توجد ديون)\n"
        
        text += "   ━━━━━━━━━━━━━━━━━━"

        # 5. استدعاء الكيبورد
        markup = get_wallet_keyboard(user_id, debt)

        if message:
            await message.answer(text, reply_markup=markup, parse_mode="HTML")
        elif callback:
            await callback.message.edit_text(text, reply_markup=markup, parse_mode="HTML")

    except Exception as e:
        logging.error(f"❌ Error in wallet logic: {e}")
        if message: await message.answer("⚠️ حدث خطأ فني أثناء جلب بياناتك.")
             
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
            text += f"{icon} <b>{sym}</b> : <code>{price:,.2f} $</code> ({chg:+.2f}%)\n"
            # إضافة أزرار العملات تحت الرسالة
            markup.add(InlineKeyboardButton(f"عرض {sym} 🪙", callback_data=f"coin_view:{user_id}:{sym}"))

    await message.answer(text, reply_markup=markup, parse_mode="HTML")
# --- 2. المستمع (الذي لا يستجيب) ---
@dp.message_handler(Text(equals=["صفقاتي", "الصفقات"], ignore_case=True), state="*")
async def listener_trades(message: types.Message):
    user_id = int(message.from_user.id)
    try:
        trades, text = await get_active_trades_report(user_id)
        
        if not trades:
            # تأكد أن دالة get_market_keyboard لا تحتوي على أخطاء أيضاً
            return await message.answer(text, reply_markup=get_market_keyboard(user_id), parse_mode="HTML")
        
        # استدعاء الكيبورد المصحح
        await message.answer(text, reply_markup=get_trades_keyboard(user_id, trades), parse_mode="HTML")
    except Exception as e:
        logging.error(f"Listener Error: {e}")
        await message.answer(f"⚠️ عذراً، حدث خطأ أثناء جلب صفقاتك: {e}")

# ==========================================
# 6. معالجات الأزرار الأساسية (Secured Callbacks)
# ==========================================
@dp.callback_query_handler(lambda c: c.data and c.data.startswith('wallet_view:'), state="*")
async def callback_wallet_view(callback_query: types.CallbackQuery):
    user_id = int(callback_query.data.split(':')[1])
    if callback_query.from_user.id != user_id:
        return await callback_query.answer("❌ هذه المحفظة ليست لك!", show_alert=True)
    await process_wallet_logic(user_id, callback_query.from_user.first_name, callback=callback_query)


@dp.callback_query_handler(Text(startswith='market_tab:'), state="*")
async def callback_market_tabs(callback_query: types.CallbackQuery):
    if not await is_authorized(callback_query): return
    
    try:
        data_parts = callback_query.data.split(':')
        user_id = int(data_parts[1])
        tab_type = data_parts[2]
        
        # الآن نستخدم قوة SQL للترتيب مباشرة بفضل العمود الجديد change_24h
        if tab_type == 'gainers':
            # جلب أعلى 5 عملات من حيث نسبة الربح
            res = supabase.table("crypto_market_simulation").select("*").order("change_24h", desc=True).limit(20).execute()
            header = "📈 <b>الأعلى ربحاً (24h):</b>"
        elif tab_type == 'losers':
            # جلب أكثر 5 عملات خسارة (من الأصغر للأكبر)
            res = supabase.table("crypto_market_simulation").select("*").order("change_24h", desc=False).limit(20).execute()
            header = "📉 <b>الأكثر خسارة (24h):</b>"
        else: # trending
            # جلب الأكثر سيولة (رواجاً) حسب الحجم
            res = supabase.table("crypto_market_simulation").select("*").order("volume_24h", desc=True).limit(20).execute()
            header = "🔥 <b>الأكثر رواجاً (السيولة):</b>"
            
        if not res.data:
            return await callback_query.answer("⚠️ لا توجد بيانات حالياً.", show_alert=True)

        text = f"📊 | <b>سـوق الـعـمـلات (Binance Mode)</b>\n━━━━━━━━━━━━━━━━━━\n{header}\n\n"
        markup = InlineKeyboardMarkup(row_width=2)
        
        for c in res.data:
            sym = c['symbol']
            price = float(c.get('current_price', 0))
            # نستخدم العمود الجديد مباشرة
            chg = float(c.get('change_24h', 0))
            
            icon = "🟢" if chg >= 0 else "🔴"
            # تنسيق السعر 4 أرقام والنسبة رقمين
            text += f"{icon} <b>{sym}</b> : <code>{price:,.2f}$</code> ({chg:+.2f}%)\n"
            
            markup.insert(InlineKeyboardButton(f"🪙 {sym}", callback_data=f"coin_view:{user_id}:{sym}"))

        # أزرار التبويبات للتنقل السريع
        markup.row(
            InlineKeyboardButton("🔥 الرائجة", callback_data=f"market_tab:{user_id}:trending"),
            InlineKeyboardButton("📈 الرابحة", callback_data=f"market_tab:{user_id}:gainers"),
            InlineKeyboardButton("📉 الخاسرة", callback_data=f"market_tab:{user_id}:losers")
        )
        markup.row(InlineKeyboardButton("🔙 العودة للمحفظة", callback_data=f"wallet:{user_id}"))

        await callback_query.message.edit_text(text, reply_markup=markup, parse_mode="HTML")

    except Exception as e:
        logging.error(f"Error in market_tab: {e}")
        await callback_query.answer("⚠️ فشل تحديث بيانات السوق.", show_alert=True)

# --- 3. الكولباك (الذي لا يستجيب للضغط) ---
import asyncio

# --- 3. الكولباك (الذي لا يستجيب للضغط + حماية وتنظيف) ---
@dp.callback_query_handler(Text(startswith='active_trades_view_'), state="*")
async def callback_view_trades(callback_query: types.CallbackQuery):
    await callback_query.answer()
    
    # تفكيك الآيدي للتحقق من المالك
    data = callback_query.data.split('_') # ['active', 'trades', 'view', 'uid']
    user_id = int(data[3])
    
    # 🛡️ الجدار الناري
    if callback_query.from_user.id != user_id:
        return await callback_query.answer("⚠️ ليس لديك صلاحية للوصول إلى لوحة غيرك!", show_alert=True)
    
    try:
        trades, text = await get_active_trades_report(user_id)
        
        # دالة حذف الرسالة في الخلفية
        async def delete_message_later(msg, delay=300):
            await asyncio.sleep(delay)
            try:
                await msg.delete()
            except:
                pass # تجاهل الخطأ لو المستخدم حذفها يدوياً
                
        if not trades:
            msg = await callback_query.message.edit_text(
                text, 
                reply_markup=get_market_keyboard(user_id), 
                parse_mode="HTML"
            )
        else:
            msg = await callback_query.message.edit_text(
                text, 
                reply_markup=get_trades_keyboard(user_id, trades), 
                parse_mode="HTML"
            )
            
        # تشغيل المؤقت (5 دقائق = 300 ثانية)
        asyncio.create_task(delete_message_later(callback_query.message, 300))
        
    except Exception as e:
        logging.error(f"Callback View Error: {e}")
        await callback_query.message.answer(f"❌ فشل عرض الصفقات.")
        
        
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
    text += f"💰 الـسـعـر الـحـالـي: <code>{price:,.2f} $</code>\n"
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
# 7. معالجات دورة الصفقة (مزودة بـ state="*")
# ==========================================

@dp.callback_query_handler(Text(startswith='setup_trade:'), state="*")
async def process_setup_trade(callback_query: types.CallbackQuery):
    if not await is_authorized(callback_query): return
    
    user_id = callback_query.from_user.id
    _, _, symbol, side = callback_query.data.split(':')
    
    try:
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
    except Exception as e:
        logging.error(f"Error in setup_trade: {e}")
        await callback_query.answer("⚠️ حدث خطأ أثناء تجهيز الصفقة.", show_alert=True)

@dp.callback_query_handler(Text(startswith='trade_cycle:'), state="*")
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

@dp.callback_query_handler(Text(startswith='trade_confirm:'), state="*")
async def process_trade_confirm(callback_query: types.CallbackQuery):
    if not await is_authorized(callback_query): return
    
    user_id = callback_query.from_user.id
    if user_id not in trade_sessions:
        return await callback_query.answer("⚠️ حدث خطأ أو الجلسة انتهت.", show_alert=True)
        
    session = trade_sessions[user_id]
    
    # حساب الهامش (Margin)
    margin_amount = session['balance'] * (session['margin_pct'] / 100.0)
    
    if margin_amount <= 0 or margin_amount > session['balance']:
        return await callback_query.answer("❌ رصيدك غير كافٍ لهذه العملية!", show_alert=True)
        
    try:
        # جلب السعر من الجدول (اللي صار bigint الآن)
        coin_res = supabase.table("crypto_market_simulation").select("current_price").eq("symbol", session['symbol']).execute()
        current_price = int(coin_res.data[0]['current_price'])
        
        # الحسابات (نضرب في الرافعة ونقسم)
        quantity = (margin_amount * session['leverage']) / current_price
        liq_price = calculate_liquidation(current_price, session['leverage'], session['side'])
        expiry = datetime.now() + DURATION_MAP[session['duration']][1]
        
        new_balance = session['balance'] - margin_amount
        
        # 🟢 1. سحب المبلغ (تحويل كل شيء لـ int إجباري)
        supabase.table("users_global_profile").update({
            "bank_balance": int(new_balance) 
        }).eq("user_id", int(user_id)).execute()
        
        # 🟢 2. فتح الصفقة (كل القيم المالية int)
        trade_data = {
            "user_id": int(user_id),
            "symbol": str(session['symbol']),
            "side": str(session['side']),
            "entry_price": int(current_price),
            "leverage": int(session['leverage']),
            "margin": int(margin_amount),
            "quantity": int(quantity),
            "liquidation_price": int(liq_price),
            "expiry_time": expiry.isoformat(),
            "is_active": True
        }
        
        supabase.table("active_trades").insert(trade_data).execute()
        
        # 3. تنظيف الجلسة
        del trade_sessions[user_id]
        
        # رسالة النجاح (للعرض نستخدم الفواصل للجمالية فقط)
        text = "✅ <b>تـم فـتـح الـصـفـقـة بـنـجـاح!</b> 🚀\n\n"
        text += f"العملة: #{session['symbol']}\n"
        text += f"النوع: {session['side']}\n"
        text += f"سعر الدخول: {int(current_price):,} $\n"
        text += f"المبلغ المحجوز: {int(margin_amount):,} $\n"
        text += f"رصيدك المتبقي: {int(new_balance):,} $"
        
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("📋 عرض صفقاتي", callback_data=f"active_trades_view:{user_id}"))
        markup.add(InlineKeyboardButton("🔙 العودة للسوق", callback_data=f"market_tab:{user_id}:trending"))
        
        await callback_query.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
          
    except Exception as e:
        logging.error(f"Trade Insert Error: {e}")
        await callback_query.answer(f"❌ خطأ: تأكد من تحويل الأعمدة لـ bigint", show_alert=True)
        
@dp.callback_query_handler(Text(startswith=('pre_risk_sl_', 'pre_risk_tp_')), state="*")
async def handle_automated_risk_selection(callback_query: types.CallbackQuery):
    try:
        data = callback_query.data.split('_')
        risk_type = data[2]
        btn_user_id = int(data[3])
        trade_id = data[4]
        
        # 🟢 التعديل الجوهري هنا: استخدم float بدلاً من int
        target_price = float(data[5]) 

        if callback_query.from_user.id != btn_user_id:
            return await callback_query.answer("⚠️ هذه الصلاحية ليست لك! 🚫", show_alert=True)

        res = supabase.table("active_trades").select("*").eq("trade_id", trade_id).execute()
        if not res.data:
            return await callback_query.answer("⚠️ الصفقة مغلقة.")
        
        trade = res.data[0]
        
        # 🟢 تأكد أيضاً أن الأسعار هنا float لكي لا تفقد الدقة في الحسابات
        entry = float(trade['entry_price'])
        liq = float(trade['liquidation_price'])
        side = trade['side']
        lev = int(trade['leverage'])
        margin = float(trade['margin'])

        # التحقق من سعر التصفية
        if risk_type == "sl":
            if side == "LONG" and target_price <= liq:
                return await callback_query.answer(f"⚠️ السعر {target_price} خلف التصفية!", show_alert=True)
            if side == "SHORT" and target_price >= liq:
                return await callback_query.answer(f"⚠️ السعر {target_price} خلف التصفية!", show_alert=True)

        # حسابات PNL
        diff = (target_price - entry) if side == "LONG" else (entry - target_price)
        pnl_pct = (diff / entry) * lev * 100
        expected_cash = margin * (pnl_pct / 100)

        label = "إيقاف الخسارة (SL)" if risk_type == "sl" else "جني الأرباح (TP)"
        status_icon = "✅ حماية" if pnl_pct > 0 else "📉 مخاطرة"
        
        # تنسيق السعر عند العرض: إذا كان كبيراً أظهره صحيحاً، إذا صغيراً أظهره بـ 4 فواصل
        p_display = f"{target_price:,}" if target_price > 10 else f"{target_price:.4f}"

        text = f"⚖️ <b>تأكيد مستهدف {label}</b>\n"
        text += f"━━━━━━━━━━━━━━\n"
        text += f"• السعر المختار: <code>{p_display}</code>\n"
        text += f"• الحالة: <b>{status_icon}</b>\n"
        text += f"• النسبة المتوقعة: <b>{pnl_pct:+.2f}%</b>\n"
        text += f"• الربح/الخسارة: <b>{expected_cash:+.2f} $</b>\n\n"
        text += "هل تريد اعتماد هذا المستهدف وحفظه؟"

        save_callback = f"conf_{risk_type}_{btn_user_id}_{trade_id}_{target_price}"
        
        markup = InlineKeyboardMarkup(row_width=1).add(
            InlineKeyboardButton("✅ نعم، تأكيد الحفظ", callback_data=save_callback),
            InlineKeyboardButton("❌ تراجع (العودة)", callback_data=f"exp_risk_{btn_user_id}_{trade_id}")
        )

        await callback_query.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
        await callback_query.answer()

    except Exception as e:
        logging.error(f"Error in automated risk: {e}")
        await callback_query.answer("⚠️ خطأ في الحسابات.. تأكد من تنسيق السعر.")
        
# =========================================================
# 8. إدارة الصفقات المفتوحة (DCA & Close) - النسخة المحصنة
# =========================================================
# --- 1. معالج فتح لوحة الإعدادات (Main Gate) ---
@dp.callback_query_handler(Text(startswith='manage_trade:'), state="*")
async def callback_manage_trade_handler(callback_query: types.CallbackQuery):
    try:
        t_id = callback_query.data.split(':')[1]
        res = supabase.table("active_trades").select("*").eq("trade_id", t_id).execute()
        
        if not res.data:
            return await callback_query.answer("⚠️ الصفقة غير موجودة أو أغلقت.", show_alert=True)
        
        trade = res.data[0]
        # حماية: التأكد أن من يفتح الإعدادات هو صاحب الصفقة
        if callback_query.from_user.id != int(trade['user_id']):
            return await callback_query.answer("⚠️ لا يمكنك إدارة صفقات الآخرين!", show_alert=True)

        coin_res = supabase.table("crypto_market_simulation").select("current_price").eq("symbol", trade['symbol']).execute()
        current_price = int(coin_res.data[0]['current_price']) if coin_res.data else int(trade['entry_price'])

        # توليد الواجهة (القائمة الرئيسية - الآباء)
        text, markup = get_trade_settings_view(trade, current_price)
        await callback_query.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
        await callback_query.answer()
    except Exception as e:
        await callback_query.answer("❌ خطأ في فتح الإعدادات.")

# --- 2. معالج التوسع (ولد الولد - الأكورديون) المعدل ليدعم الكسور ---
@dp.callback_query_handler(Text(startswith='exp_'), state="*")
async def handle_expansion_protected(callback_query: types.CallbackQuery):
    try:
        # تفكيك: exp _ section _ uid _ tid
        data = callback_query.data.split('_') 
        section = data[1]
        btn_user_id = int(data[2])
        t_id = data[3]
        
        # 🛡️ حماية
        if callback_query.from_user.id != btn_user_id:
            return await callback_query.answer("⚠️ مبعسس! هذه الأزرار ليست لك. 🚫", show_alert=True)

        # جلب بيانات الصفقة
        res = supabase.table("active_trades").select("*").eq("trade_id", t_id).execute()
        if not res.data:
            return await callback_query.answer("⚠️ الصفقة غير موجودة.")
        
        trade = res.data[0]
        
        # جلب السعر الحالي
        coin_res = supabase.table("crypto_market_simulation").select("current_price").eq("symbol", trade['symbol']).execute()
        
        # 🟢 التعديل الجوهري: استخدام float بدلاً من int لضمان عدم توقف الكود
        current_price = float(coin_res.data[0]['current_price']) if coin_res.data else float(trade['entry_price'])

        # توليد الواجهة الفرعية (الأبناء) - نمرر السعر كـ float
        text, markup = get_trade_settings_view(trade, current_price, expand_section=section)
        
        await callback_query.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
        await callback_query.answer()

    except Exception as e:
        import logging
        logging.error(f"Expansion Error: {e}")
        await callback_query.answer("❌ حدث خطأ داخلي أثناء فتح القائمة.")

# --- 3. معالج التعزيز (DCA) المحمي ---
@dp.callback_query_handler(Text(startswith='dca_'), state="*")
async def handle_dca_protected(callback_query: types.CallbackQuery):
    data = callback_query.data.split('_') # dca_percent_uid_tid
    percent, btn_user_id, t_id = int(data[1]), int(data[2]), data[3]
    
    if callback_query.from_user.id != btn_user_id:
        return await callback_query.answer("⚠️ غير مسموح بالبعسسة هنا! 🚫", show_alert=True)

    res = supabase.table("active_trades").select("*").eq("trade_id", t_id).execute()
    user_res = supabase.table("users_global_profile").select("bank_balance").eq("user_id", btn_user_id).execute()
    trade = res.data[0]
    bank_balance = int(user_res.data[0]['bank_balance'])
    
    dca_amount = int(bank_balance * (percent / 100))
    if dca_amount < 1: return await callback_query.answer("❌ رصيدك لا يسمح.", show_alert=True)

    coin_res = supabase.table("crypto_market_simulation").select("current_price").eq("symbol", trade['symbol']).execute()
    current_price = int(coin_res.data[0]['current_price'])

    # معادلات Averaging
    old_margin, old_entry, lev, side = int(trade['margin']), int(trade['entry_price']), int(trade['leverage']), trade['side']
    old_units = (old_margin * lev) / old_entry
    new_units_added = (dca_amount * lev) / current_price
    total_units = old_units + new_units_added
    new_margin = old_margin + dca_amount
    new_entry_price = int((new_margin * lev) / total_units)
    new_liq = int(calculate_liquidation(new_entry_price, lev, side))

    # التحديث
    supabase.table("users_global_profile").update({"bank_balance": bank_balance - dca_amount}).eq("user_id", btn_user_id).execute()
    supabase.table("active_trades").update({
        "margin": new_margin, "entry_price": new_entry_price, 
        "liquidation_price": new_liq, "quantity": int(total_units)
    }).eq("trade_id", t_id).execute()

    await callback_query.answer(f"🚀 تم التعزيز بـ {dca_amount:,} $\nالسعر الجديد: {new_entry_price:,}", show_alert=True)
    await callback_view_trades(callback_query)

# --- 4. معالج الإغلاق الجزئي (Partial Close) المحمي ---
@dp.callback_query_handler(Text(startswith='pcl_'), state="*")
async def handle_partial_protected(callback_query: types.CallbackQuery):
    data = callback_query.data.split('_') # pcl_percent_uid_tid
    percent, btn_user_id, t_id = int(data[1]), int(data[2]), data[3]
    
    if callback_query.from_user.id != btn_user_id:
        return await callback_query.answer("⚠️ الصفقة محمية ضد المبعسسين! 🚫", show_alert=True)

    res = supabase.table("active_trades").select("*").eq("trade_id", t_id).execute()
    trade = res.data[0]
    
    coin_res = supabase.table("crypto_market_simulation").select("current_price").eq("symbol", trade['symbol']).execute()
    current_price = int(coin_res.data[0]['current_price'])

    # حسابات الإغلاق
    old_margin, old_entry, lev, side = int(trade['margin']), int(trade['entry_price']), int(trade['leverage']), trade['side']
    margin_to_close = int(old_margin * (percent / 100))
    pnl_pct = (current_price - old_entry) / old_entry if side == 'LONG' else (old_entry - current_price) / old_entry
    pnl_amount = int(margin_to_close * pnl_pct * lev)
    return_to_bank = max(0, margin_to_close + pnl_amount)

    # تحديث البنك والصفقة
    user_res = supabase.table("users_global_profile").select("bank_balance").eq("user_id", btn_user_id).execute()
    new_bank = int(user_res.data[0]['bank_balance']) + return_to_bank
    supabase.table("users_global_profile").update({"bank_balance": new_bank}).eq("user_id", btn_user_id).execute()

    new_margin = old_margin - margin_to_close
    new_qty = int(trade['quantity'] * (1 - (percent/100)))
    supabase.table("active_trades").update({"margin": new_margin, "quantity": new_qty}).eq("trade_id", t_id).execute()
    
    await callback_query.answer(f"✅ إغلاق {percent}% موفق!\nعائد للمحفظة: {return_to_bank:,} $", show_alert=True)
    await callback_view_trades(callback_query)

# --- 5. بوابة التأكيد والعودة (Security Gate & Back) ---
# --- 5. بوابة التأكيد والعودة (Security Gate & Back) ---
@dp.callback_query_handler(Text(startswith='conf_'), state="*")
async def security_gate_protected(callback_query: types.CallbackQuery):
    # تفكيك: conf_نوع_نسبة_ايدي_صفقة (مثال: conf_cl_100_12345_6789)
    data = callback_query.data.split('_')
    action_type = data[1] # cl أو dca
    percent = data[2]
    btn_user_id = int(data[3])
    t_id = data[4]

    # 🛡️ الجدار الناري ضد المبعسسين
    if callback_query.from_user.id != btn_user_id:
        return await callback_query.answer("⚠️ لا تتدخل في صفقات غيرك! 🚫", show_alert=True)

    # جلب بيانات الصفقة للتأكد من وجودها
    res = supabase.table("active_trades").select("symbol").eq("trade_id", t_id).execute()
    if not res.data: 
        return await callback_query.message.edit_text("⚠️ هذه الصفقة أغلقت أو لم تعد موجودة.")
    
    symbol = res.data[0]['symbol']
    action_name = "إغلاق المركز بالكامل (100%)" if action_type == 'cl' else "تعزيز المركز بكامل الرصيد (100%)"
    
    text = f"⚠️ <b>تأكيـد الإجـراء النهـائي: #{symbol}</b>\n"
    text += f"━━━━━━━━━━━━━━━━━━\n"
    text += f"• الإجراء: <b>{action_name}</b>\n"
    text += "• التحذير: هذا القرار سيتم تنفيذه فوراً بسعر السوق.\n\n"
    text += "<b>هل أنت متأكد من قرارك؟</b>"
    
    # الزر النهائي يذهب لـ exe_ (التنفيذ الفعلي)
    target_callback = f"exe_{action_type}_{percent}_{btn_user_id}_{t_id}"
    
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("✅ نعم، نفذ الآن", callback_data=target_callback),
        InlineKeyboardButton("❌ لا، تراجع", callback_data=f"back_ts_{btn_user_id}_{t_id}")
    )
    
    await callback_query.message.edit_text(text, reply_markup=markup, parse_mode="HTML")

# --- زر العودة (Back Logic) ---
@dp.callback_query_handler(Text(startswith='back_ts_'), state="*")
async def back_to_settings_protected(callback_query: types.CallbackQuery):
    data = callback_query.data.split('_') # back_ts_uid_tid
    btn_user_id = int(data[2])
    t_id = data[3]
    
    if callback_query.from_user.id != btn_user_id:
        return await callback_query.answer("⚠️ الصلاحية منتهية.")

    res = supabase.table("active_trades").select("*").eq("trade_id", t_id).execute()
    if not res.data: return await callback_query.answer("الصفقة مغلقة.")
    
    trade = res.data[0]
    coin_res = supabase.table("crypto_market_simulation").select("current_price").eq("symbol", trade['symbol']).execute()
    current_price = int(coin_res.data[0]['current_price'])

    # العودة للواجهة الرئيسية للإعدادات (الآباء)
    text, markup = get_trade_settings_view(trade, current_price)
    await callback_query.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    await callback_query.answer("تم الرجوع")
    
# --- معالج الحفظ النهائي في قاعدة البيانات (للـ SL و الـ TP) ---
@dp.callback_query_handler(Text(startswith=('conf_sl_', 'conf_tp_')), state="*")
async def commit_risk_to_db(callback_query: types.CallbackQuery):
    data = callback_query.data.split('_')
    risk_type = data[1]
    btn_user_id = int(data[2])
    t_id = data[3]
    new_price = int(data[4])

    if callback_query.from_user.id != btn_user_id:
        return await callback_query.answer("⚠️ عذراً، لا تملك الصلاحية! 🚫", show_alert=True)

    try:
        column_name = "stop_loss" if risk_type == "sl" else "take_profit"
        label = "وقف الخسارة" if risk_type == "sl" else "جني الأرباح"

        supabase.table("active_trades").update({
            column_name: new_price
        }).eq("trade_id", t_id).execute()
        
        await callback_query.answer(f"✅ تم حفظ {label} بنجاح!", show_alert=True)
        
        # بعد الحفظ بنجاح، نعيده إلى القائمة الرئيسية (صفقاتي) لتحديث الواجهة تلقائياً
        # نقوم باستدعاء معالج `callback_view_trades` عبر تغيير البيانات مؤقتاً
        callback_query.data = f"active_trades_view_{btn_user_id}"
        await callback_view_trades(callback_query)
        
    except Exception as e:
        logging.error(f"Error in commit_risk: {e}")
        await callback_query.answer("❌ حدث خطأ في قاعدة البيانات، حاول مجدداً.")

# --- معالج الفتح الرئيسي المحمي (من قائمة الصفقات للوحة الإعدادات) ---
@dp.callback_query_handler(Text(startswith='manage_trade_'), state="*")
async def callback_manage_trade_handler(callback_query: types.CallbackQuery):
    try:
        # هنا غيرنا التقسيم لأن الداتا أصبحت manage_trade_tid
        t_id = callback_query.data.split('_')[2] 
        res = supabase.table("active_trades").select("*").eq("trade_id", t_id).execute()
        
        if not res.data:
            return await callback_query.answer("⚠️ الصفقة غير موجودة أو أغلقت.", show_alert=True)
        
        trade = res.data[0]
        if callback_query.from_user.id != int(trade['user_id']):
            return await callback_query.answer("⚠️ لا يمكنك إدارة صفقات الآخرين!", show_alert=True)

        coin_res = supabase.table("crypto_market_simulation").select("current_price").eq("symbol", trade['symbol']).execute()
        current_price = int(coin_res.data[0]['current_price']) if coin_res.data else int(trade['entry_price'])

        text, markup = get_trade_settings_view(trade, current_price)
        await callback_query.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
        await callback_query.answer()
    except Exception as e:
        await callback_query.answer("❌ خطأ في فتح الإعدادات.")
        


# --- معالج العودة السريع (Back to Risk Menu) ---
@dp.callback_query_handler(Text(startswith='exp_risk_'), state="*")
async def handle_return_to_risk_menu(callback_query: types.CallbackQuery):
    # هذا المعالج يستخدمه زر "❌ تراجع" ليعيد المستخدم لقائمة الأهداف المقترحة
    data = callback_query.data.split('_') # exp_risk_uid_tid
    btn_user_id = int(data[2])
    t_id = data[3]
    
    if callback_query.from_user.id != btn_user_id:
        return await callback_query.answer("⚠️")

    res = supabase.table("active_trades").select("*").eq("trade_id", t_id).execute()
    trade = res.data[0]
    coin_res = supabase.table("crypto_market_simulation").select("current_price").eq("symbol", trade['symbol']).execute()
    current_price = int(coin_res.data[0]['current_price'])

    # استدعاء دالة العرض مع توسيع قسم المخاطر (risk)
    text, markup = get_trade_settings_view(trade, current_price, expand_section="risk")
    await callback_query.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    await callback_query.answer()
    
# ==========================================
# 9. إدارة الأموال والقروض (المطورة)
# ==========================================
# --- [ 1. بدء عملية التحويل الداخلي ] ---

@dp.callback_query_handler(Text(startswith='transfer_flow:'), state="*")
async def transfer_init(callback_query: types.CallbackQuery, state: FSMContext):
    data = callback_query.data.split(':')
    user_id = int(data[1])
    direction = data[2] # to_bank أو to_wallet
    
    if callback_query.from_user.id != user_id:
        return await callback_query.answer("❌ لا يمكنك التحكم بأموال غيرك!", show_alert=True)
    
    # حفظ الاتجاه في الذاكرة المؤقتة
    await state.update_data(trans_direction=direction)
    await BankTransfer.waiting_for_amount.set()
    
    prompt = "📥 <b>إيداع للتداول</b>\nأرسل الآن المبلغ الذي تريد تحويله إلى حساب التداول:" if direction == "to_bank" else \
             "📤 <b>سحب للمحفظة</b>\nأرسل الآن المبلغ الذي تريد سحبه لمحفظتك الشخصية:"
    
    await callback_query.message.answer(prompt, parse_mode="HTML")
    await callback_query.answer()

# --- [ 2. معالجة المبلغ وتنفيذ التحديث في سوبابيس ] ---
@dp.message_handler(state=BankTransfer.waiting_for_amount)
async def process_transfer_amount(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    
    # تحويل المدخل إلى رقم صحيح فوراً
    try:
        # نستخدم int(float()) للتعامل مع أي إدخال خاطئ وتحويله لصحيح
        amount = int(float(message.text.replace(',', '.')))
        if amount <= 0: raise ValueError
    except:
        return await message.reply("⚠️ يرجى إرسال رقم صحيح فقط (بدون فواصل).")

    state_data = await state.get_data()
    direction = state_data.get('trans_direction')
    
    # جلب بيانات المستخدم
    user_data = await get_user_data(user_id)
    if not user_data: return await state.finish()

    # التأكد من قراءة الأرقام كـ int
    wallet_bal = int(user_data.get('wallet', 0) or 0)
    bank_bal = int(user_data.get('bank_balance', 0) or 0)

    if direction == "to_bank":
        if amount > wallet_bal:
            return await message.reply(f"❌ رصيد المحفظة غير كافٍ. المتاح: {wallet_bal} $")
        
        # تحديث سوبابيس (أرقام صحيحة)
        supabase.table("users_global_profile").update({
            "wallet": wallet_bal - amount,
            "bank_balance": bank_bal + amount
        }).eq("user_id", user_id).execute()
        
    else: # سحب للمحفظة
        is_safe, health_msg = await check_financial_health(user_id, amount, "WITHDRAW")
        if not is_safe: return await message.reply(health_msg)
        
        if amount > bank_bal:
            return await message.reply(f"❌ رصيد التداول غير كافٍ. المتاح: {bank_bal} $")

        supabase.table("users_global_profile").update({
            "bank_balance": bank_bal - amount,
            "wallet": wallet_bal + amount
        }).eq("user_id", user_id).execute()

    await message.answer(f"✅ تم تحويل <b>{amount} $</b> بنجاح!", parse_mode="HTML")
    await state.finish()
    # تحديث شكل المحفظة
    await process_wallet_logic(user_id, message.from_user.first_name, message=message)
   
# --- قسم القروض ---
@dp.callback_query_handler(Text(startswith='repay_loan:'), state="*")
async def repay_loan_handler(callback_query: types.CallbackQuery):
    try:
        user_id = int(callback_query.data.split(':')[1])
        
        # جلب البيانات مباشرة لضمان أحدث الأرقام من قاعدة البيانات
        res = supabase.table("users_global_profile").select("bank_balance, debt_balance").eq("user_id", user_id).execute()
        
        if not res.data:
            return await callback_query.answer("❌ لم يتم العثور على بياناتك.", show_alert=True)
            
        user_data = res.data[0]
        
        # تحويل القيم إلى int لضمان عدم وجود أخطاء حسابية (التعامل مع None كـ 0)
        debt = int(user_data.get('debt_balance', 0) or 0)
        bank_bal = int(user_data.get('bank_balance', 0) or 0)
        
        # 1. التحقق إذا كان هناك دين أصلاً
        if debt <= 0:
            return await callback_query.answer("✅ ليس لديك أي ديون مستحقة لتسديدها!", show_alert=True)
            
        # 2. التحقق من كفاية الرصيد في البنك
        if bank_bal < debt:
            missing = debt - bank_bal
            return await callback_query.answer(f"❌ رصيدك ({bank_bal:,}$) غير كافٍ.\nتحتاج لجمع {missing:,}$ إضافية للسداد.", show_alert=True)
        
        # 3. تنفيذ عملية الخصم والتصفير في سوبابيس
        new_bank_balance = bank_bal - debt
        
        supabase.table("users_global_profile").update({
            "bank_balance": new_bank_balance,
            "debt_balance": 0
        }).eq("user_id", user_id).execute()
        
        # 4. إشعار النجاح وتحديث المحفظة
        await callback_query.answer(f"✅ تم سداد مبلغ {debt:,}$ بنجاح!\nرصيدك المتبقي: {new_bank_balance:,}$", show_alert=True)
        
        # تحديث واجهة المحفظة ليرى المستخدم أن الدين أصبح 0 وزر القرض عاد
        await process_wallet_logic(user_id, callback_query.from_user.first_name, callback=callback_query)

    except Exception as e:
        logging.error(f"❌ Error in repay_loan: {e}")
        await callback_query.answer("⚠️ حدث خطأ فني أثناء السداد.", show_alert=True)
        
@dp.callback_query_handler(Text(startswith='loan_menu:'), state="*")
async def loan_menu(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    
    # جلب بيانات المستخدم
    user_data = await get_user_data(user_id)
    if not user_data: return
    
    # 1. التحقق من الدين الحالي
    current_debt = int(user_data.get('debt_balance', 0) or 0)
    # 2. التحقق من إجمالي التداولات (كمعيار للأهلية أو استخدام حقل مخصص إذا أردت منعه للأبد)
    # هنا سنعتمد على وجود دين حالي، أو يمكنك إضافة شرط "مرة واحدة" بناءً على منطقك الخاص
    
    if current_debt > 0:
        return await callback_query.answer("⚠️ لديك قرض نشط بالفعل، يجب سداده أولاً!", show_alert=True)

    loan_amount = 10000  # المبلغ الثابت الذي طلبته
    
    markup = InlineKeyboardMarkup()
    # زر التنفيذ يرسل المبلغ الثابت
    markup.add(InlineKeyboardButton(f"💰 اقتراض {loan_amount:,} $ (مرة واحدة)", callback_data=f"exec_loan:{user_id}:{loan_amount}"))
    markup.add(InlineKeyboardButton("🔙 عودة للمحفظة", callback_data=f"wallet_view:{user_id}"))
    
    text = (
        f"🏦 | <b>مـركـز الائـتـمـان والـقـروض</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"مرحباً بك في نظام القروض الموحد.\n"
        f"يمكنك الحصول على سيولة فورية لبدء تداولاتك.\n\n"
        f"💵 الـمبلغ الـمتاح لك: <b>{loan_amount:,} $</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"<i>* تنبيه: سيتم خصم القرض من أرباحك لاحقاً عند السداد.</i>"
    )

    await callback_query.message.edit_text(text, reply_markup=markup, parse_mode="HTML")

@dp.callback_query_handler(Text(startswith='exec_loan:'), state="*")
async def exec_loan_handler(callback_query: types.CallbackQuery):
    data = callback_query.data.split(':')
    user_id = int(data[1])
    loan_amount = int(data[2]) # التأكد أنه رقم صحيح
    
    if callback_query.from_user.id != user_id:
        return await callback_query.answer("❌ خطأ في الهوية", show_alert=True)
    
    user_data = await get_user_data(user_id)
    if not user_data: return

    # حساب القيم الجديدة كأرقام صحيحة
    new_bank = int(user_data.get('bank_balance', 0) or 0) + loan_amount
    new_debt = int(user_data.get('debt_balance', 0) or 0) + loan_amount

    try:
        # التحديث في سوبابيس (أعمدة الـ int)
        supabase.table("users_global_profile").update({
            "bank_balance": new_bank,
            "debt_balance": new_debt
            # ملاحظة: حذفنا last_loan_date لتجنب خطأ 22P02 إذا لم يكن العمود جاهزاً
        }).eq("user_id", user_id).execute()
        
        await callback_query.answer(f"✅ تم منحك قرض بقيمة {loan_amount:,} $ بنجاح!", show_alert=True)
        
        # تحديث واجهة المحفظة فوراً ليرى المستخدم الرصيد الجديد
        await process_wallet_logic(user_id, callback_query.from_user.first_name, callback=callback_query)
        
    except Exception as e:
        logging.error(f"❌ Loan Error: {e}")
        await callback_query.answer("❌ فشل في تحديث قاعدة البيانات، حاول لاحقاً.", show_alert=True)
        
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
    # أ) إعداد سيرفر الويب (لبقاء البوت متصلاً على Render)
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

    # ج) تشغيل البوت (لإصدار Aiogram 2.x)
    try:
        logging.info("🚀 جاري إقلاع محرك التليجرام...")
        
        # 1. تخطي الرسائل القديمة المتراكمة أثناء الإيقاف
        await dp.skip_updates()
        
        # 2. بدء استقبال الرسائل والطلبات
        await dp.start_polling()
        
    except Exception as e:
        logging.error(f"❌ خطأ في تشغيل البوت: {e}")
    finally:
        # الإغلاق الآمن لتجنب تحذيرات (NoneType)
        logging.info("🛑 جاري إغلاق الاتصال بأمان...")
        await bot.close()
        await dp.storage.close()
        await dp.storage.wait_closed()

if __name__ == '__main__':
    # دمج جميع العمليات في مسار واحد (Event Loop) يمنع التضارب
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(main_startup())
    except KeyboardInterrupt:
        logging.info("🛑 تم إيقاف البوت يدوياً.")
