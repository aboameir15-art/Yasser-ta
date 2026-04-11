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
import math
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
# --- [ محرك تحليل الحساب المطور ] ---
# ==========================================
async def get_trading_account_snapshot(user_id):
    try:
        user_res = supabase.table("users_global_profile").select("bank_balance").eq("user_id", user_id).execute()
        free_cash = float(user_res.data[0]['bank_balance']) if user_res.data else 0.0
        
        trades = supabase.table("active_trades").select("*").eq("user_id", user_id).eq("is_active", True).execute()
        
        total_used_margin = 0.0
        total_unrealized_pnl = 0.0
        
        for t in trades.data:
            mar = float(t['margin'])
            total_used_margin += mar
            # ... (حساب pnl_pct كما في السابق)
            total_unrealized_pnl += (mar * pnl_pct * float(t['leverage']))

        # 🎯 المنطق الجديد
        total_balance = free_cash + total_used_margin # هذا الـ 1000 في مثالك
        total_equity = total_balance + total_unrealized_pnl # القيمة مع الربح/الخسارة
        
        return {
            "free_cash": round(free_cash, 2),
            "used_margin": round(total_used_margin, 2),
            "total_balance": round(total_balance, 2), # الرصيد الكلي المجموع
            "total_pnl": round(total_unrealized_pnl, 2),
            "total_equity": round(total_equity, 2)
        }
    except Exception as e:
        # التعامل مع الخطأ
        return {"free_cash": 0, "used_margin": 0, "total_balance": 0, "total_pnl": 0, "total_equity": 0}
              

async def trade_reaper():
    """
    رادار التصفية الشاملة + منفذ الطلبات المعلقة
    """
    while True:
        try:
            # --- الجزء الأول: جلب كافة البيانات من قاعدة البيانات ---
            # جلب الصفقات (نشطة ومعلقة)
            trades_res = supabase.table("active_trades").select("*").execute()
            all_trades = trades_res.data
            
            # جلب أسعار السوق الحالية
            market_res = supabase.table("crypto_market_simulation").select("symbol, current_price").execute()
            market_prices = {item['symbol']: float(item['current_price']) for item in market_res.data}

            if not all_trades:
                await asyncio.sleep(5)
                continue

            # تصنيف الصفقات (نشطة للمراقبة، ومعلقة للتنفيذ)
            active_trades_by_user = {}
            pending_trades = [t for t in all_trades if not t['is_active']]
            active_trades_list = [t for t in all_trades if t['is_active']]

            # تجميع الصفقات النشطة حسب المستخدم لحساب التصفية
            for t in active_trades_list:
                uid = t['user_id']
                if uid not in active_trades_by_user: active_trades_by_user[uid] = []
                active_trades_by_user[uid].append(t)

            # --- الجزء الثاني: تنفيذ الطلبات المعلقة (Limit Orders) ---
            for pt in pending_trades:
                sym = pt['symbol']
                target_p = float(pt['entry_price'])
                current_p = market_prices.get(sym)
                side = pt['side']
                
                if not current_p: continue

                # شروط التنفيذ: 
                # في الشراء (Long): يتفعل الطلب إذا نزل السعر للمنطقة أو تحتها
                # في البيع (Short): يتفعل الطلب إذا صعد السعر للمنطقة أو فوقها
                should_activate = False
                if side == 'LONG' and current_p <= target_p:
                    should_activate = True
                elif side == 'SHORT' and current_p >= target_p:
                    should_activate = True

                if should_activate:
                    # تحويل الصفقة إلى نشطة (True)
                    supabase.table("active_trades").update({"is_active": True}).eq("trade_id", pt['trade_id']).execute()
                    await bot.send_message(
                        pt['user_id'],
                        f"⚡ <b>تـم تـفـعـيـل الـطـلـب الـمـعـلـق!</b>\n\n"
                        f"العملة: #{sym}\n"
                        f"سعر الدخول المختار: <code>{target_p:,.4f}$</code>\n"
                        f"سعر السوق الحالي: <code>{current_p:,.4f}$</code>\n"
                        f"الصفقة الآن قيد التداول 🚀",
                        parse_mode="HTML"
                    )

            # --- الجزء الثالث: رادار التصفية الشاملة (حذف السجلات) ---
            for uid, trades in active_trades_by_user.items():
                user_res = supabase.table("users_global_profile").select("bank_balance").eq("user_id", uid).execute()
                if not user_res.data: continue
                
                bank_balance = float(user_res.data[0].get('bank_balance', 0.0))
                total_used_margin = 0.0
                total_unrealized_pnl = 0.0
                
                for t in trades:
                    sym = t['symbol']
                    entry = float(t['entry_price'])
                    margin = float(t['margin'])
                    lev = float(t['leverage'])
                    current_p = market_prices.get(sym, entry)

                    total_used_margin += margin
                    pnl_pct = (current_p - entry) / entry if t['side'] == 'LONG' else (entry - current_p) / entry
                    total_unrealized_pnl += (margin * pnl_pct * lev)

                total_account_balance = bank_balance + total_used_margin
                
                # 💀 قرار الإعدام (التصفية)
                if total_unrealized_pnl <= -total_account_balance:
                    # 1. تصفير الحساب والديون في البروفايل
                    supabase.table("users_global_profile").update({
                        "bank_balance": 0.0,
                        "debt_balance": 0.0
                    }).eq("user_id", uid).execute()
                    
                    # 2. حذف (مسح) كافة الصفقات النشطة لهذا المستخدم نهائياً
                    supabase.table("active_trades").delete().eq("user_id", uid).eq("is_active", True).execute()
                    
                    await bot.send_message(
                        uid, 
                        f"💀 <b>تـصـفـيـة شـامـلـة (إبادة الحساب)!</b>\n\n"
                        f"تم مسح كافة صفقاتك النشطة وتصفير رصيدك وقرضك بسبب تجاوز الخسائر للضمان المتاح.\n"
                        f"يمكنك البدء من جديد الآن.",
                        parse_mode="HTML"
                    )

        except Exception as e:
            logging.error(f"❌ Reaper/Executor Error: {e}")
            
        await asyncio.sleep(5)
        
# ==========================================
# 1. الدوال الحسابية الأساسية (Math Core)
# ==========================================
async def get_user_bank_balance(user_id):
    """جلب رصيد البنك بدقة الكسور العشرية"""
    try:
        res = supabase.table("users_global_profile").select("bank_balance").eq("user_id", user_id).execute()
        if res.data:
            return float(res.data[0]['bank_balance'])
        return 0.0
    except Exception as e:
        logging.error(f"Error getting bank balance: {e}")
        return 0.0

def calculate_liquidation(entry_price, leverage, side, margin_amount=None, quantity=None):
    """
    حساب سعر التصفية الديناميكي.
    إذا تم تمرير الهامش والكمية، سيحسب السعر بدقة بناءً على المبلغ المدفوع.
    """
    entry = float(entry_price)
    lev = float(leverage)
    
    # إذا لم تتوفر بيانات الهامش، نستخدم المعادلة التقليدية (تصفية نظرية)
    if margin_amount is None or quantity is None or quantity == 0:
        if side == 'LONG':
            return round(entry * (1 - (1.0 / lev)), 6)
        else: 
            return round(entry * (1 + (1.0 / lev)), 6)

    # الحساب الاحترافي بناءً على "المخاطرة الفعلية":
    # السعر الذي تخسر فيه "الهامش" بالكامل
    if side == 'LONG':
        # سعر التصفية = سعر الدخول - (الهامش / الكمية)
        liq_price = entry - (float(margin_amount) / float(quantity))
    else:
        # سعر التصفية = سعر الدخول + (الهامش / الكمية)
        liq_price = entry + (float(margin_amount) / float(quantity))
    
    return max(0, round(liq_price, 6))
    

def generate_candle_chart(direction):
    """تمثيل مرئي بسيط لاتجاه الحركة الحالية"""
    if direction == 'UP':
        return "📉 ⇠ |---🟩---|\n⇠ 🚀 صعود إيجابي"
    else:
        return "📈 ⇠ |---🟥---|\n⇠ 🩸 هبوط سلبي"

# ==========================================
# 2. إدارة الأمان المالي (Financial Health)
# ==========================================

async def get_user_data(user_id):
    """جلب الملف الشخصي الكامل للمستخدم"""
    try:
        res = supabase.table("users_global_profile").select("*").eq("user_id", user_id).execute()
        return res.data[0] if res.data else None
    except:
        return None

async def check_financial_health(user_id, amount, action="WITHDRAW"):
    """
    محرك الحماية: يمنع التلاعب بالرصيد في حال وجود:
    1. ديون نشطة.
    2. صفقات مفتوحة تحجز الهامش (Margin Lock).
    """
    data = await get_user_data(user_id)
    if not data: return False, "❌ حسابك غير مسجل في النظام."
    
    bank_bal = float(data.get('bank_balance', 0.0))
    debt = float(data.get('debt_balance', 0.0))
    
    # 🔍 حساب الهامش المحجوز فعلياً في السوق الآن
    trades_res = supabase.table("active_trades").select("margin").eq("user_id", user_id).eq("is_active", True).execute()
    locked_margin = sum(float(t['margin']) for t in trades_res.data) if trades_res.data else 0.0
    
    # الكاش المتاح للسحب = رصيد البنك - الهامش المحجوز
    available_cash = max(0.0, bank_bal - locked_margin)

    if action == "WITHDRAW":
        # منع السحب في حال وجود دين
        if debt > 0:
            return False, f"⚠️ لا يمكنك السحب! لديك دين مستحق بقيمة <code>{debt:,.2f} $</code>.\nيجب سداد الدين أولاً."
        
        # منع سحب المبالغ التي تُستخدم حالياً كضمان لصفقات مفتوحة
        if amount > available_cash:
            return False, (
                f"⚠️ عذراً، السيولة غير كافية للسحب.\n"
                f"• المتاح فعلياً: <code>{available_cash:,.2f} $</code>\n"
                f"• المحجوز في الصفقات: <code>{locked_margin:,.2f} $</code>"
            )
    
    elif action == "BORROW":
        # شروط الاقتراض: لا دين سابق + حد أدنى للرصيد
        if debt > 0:
            return False, f"⚠️ لديك قرض نشط بقيمة <code>{debt:,.2f} $</code>. سدده لتتمكن من الاقتراض مجدداً."
        if bank_bal < 10.0:
            return False, "⚠️ رصيدك أقل من 10$، لا تملك الأهلية الائتمانية الكافية للقرض."
            
    return True, "Success"

# ==========================================
# 3. إدارة الصفقات النشطة (دعم الفواصل العشرية)
# ==========================================
async def get_active_trades_report(user_id):
    try:
        # 1. جلب بيانات الحساب الشاملة (Snapshot)
        account = await get_trading_account_snapshot(user_id)
        
        # السيولة الفعلية المحركة للحساب
        equity = account['total_equity'] 
        # مجموع أرباح وخسائر الصفقات المفتوحة حالياً
        total_pnl_all = account['total_pnl'] 
        
        res = supabase.table("active_trades").select("*").eq("user_id", int(user_id)).eq("is_active", True).execute()
        trades = res.data
        
        if not trades:
            return None, "📋 <b>لا توجد صفقات مفتوحة حالياً.</b>"

        pnl_all_emoji = "🟢" if total_pnl_all >= 0 else "🔴"
        
        # 2. الهيدر المختصر (صافي القيمة والارباح فقط)
        report_text = f"📋 | <b>مـراكز الـتداول الـنشطة</b>\n"
        report_text += "━━━━━━━━━━━━━━━━━━\n"

        # 3. عرض تفاصيل الصفقات
        for trade in trades:
            symbol = trade['symbol']
            side = trade['side']
            entry = float(trade['entry_price'])
            lev = float(trade['leverage'])
            margin = float(trade['margin'])
            quantity = float(trade.get('quantity', 0))
            
            # جلب سعر السوق اللحظي
            coin_res = supabase.table("crypto_market_simulation").select("current_price").eq("symbol", symbol).execute()
            current_price = float(coin_res.data[0]['current_price']) if coin_res.data else entry

            # حساب PNL الصفقة المنفردة
            pnl_pct = (current_price - entry) / entry if side == 'LONG' else (entry - current_price) / entry
            pnl_amount = margin * pnl_pct * lev
            pnl_emoji = "💰" if pnl_amount >= 0 else "📉"

            fmt = lambda p: f"{p:,.4f}" if p < 1 else f"{p:,.2f}"
            side_str = "🟢 LONG" if side == 'LONG' else "🔴 SHORT"

            report_text += f"<b>#{symbol} | {side_str} {int(lev)}x</b>\n"
            report_text += f"• الـكمية: <code>{quantity:,.4f}</code>\n"
            report_text += f"• المـبلغ الـمستخدم: <code>{margin:,.2f} $</code>\n"
            report_text += f"• سـعر الـدخول: <code>{fmt(entry)}</code>\n"
            report_text += f"• الـسعر الحالي: <code>{fmt(current_price)}</code>\n"
            report_text += f"{pnl_emoji} الـربح/الخسارة: <b>{pnl_amount:+.2f} $</b>\n"
            report_text += "━━━━━━━━━━━━━━━━━━\n"
            
        return trades, report_text

    except Exception as e:
        import logging
        logging.error(f"Error in trade report: {e}")
        return None, "❌ حدث خطأ أثناء جلب التقرير."
        

# --- دالة حساب السعر المستهدف (دعم الكسور العشرية) ---
def calc_price(base_price, roe_pct, is_tp, side, lev):
    """
    تحسب السعر المطلوب للوصول لنسبة ROE معينة.
    ROE = (Target - Entry) / Entry * Lev * 100
    """
    base_price = float(base_price)
    lev = float(lev)
    # تحويل نسبة الـ ROE إلى نسبة تحرك السعر
    move_pct = (roe_pct / 100.0) / lev
    
    if side == "LONG":
        target = base_price * (1 + move_pct) if is_tp else base_price * (1 - move_pct)
    else:
        target = base_price * (1 - move_pct) if is_tp else base_price * (1 + move_pct)
    
    # نرجع السعر بـ 6 أرقام عشرية لضمان الدقة في كل العملات
    return round(target, 6)
    
    # --- توليد واجهة الإعدادات ---
# ==========================================
# --- [ توليد واجهة الإعدادات المطورة ] ---
# ==========================================
def get_trade_settings_view(trade, current_price, expand_section=None):
    symbol = trade['symbol']
    # 🟢 تعديل: جلب البيانات كـ float لضمان الدقة
    entry = float(trade['entry_price'])
    liq = float(trade['liquidation_price'])
    t_id = str(trade['trade_id'])
    u_id = str(trade['user_id'])
    c_price = float(current_price)
    
    # دالة تنسيق السعر الذكية (4 أرقام للأجزاء، 2 للعملات الكبيرة)
    fmt = lambda p: f"{p:,.4f}" if p < 1 else f"{p:,.2f}"

    text = f"⚙️ <b>لوحة تحكم المركز: #{symbol}</b>\n"
    text += f"━━━━━━━━━━━━━━━━━━\n"
    text += f"• سعر الدخول: <code>{fmt(entry)}</code>\n"
    text += f"• السعر الحالي: <code>{fmt(c_price)}</code>\n"
    text += f"• التصفية: <pre>{fmt(liq)}</pre> ⚠️\n"
    text += f"━━━━━━━━━━━━━━━━━━\n"

    markup = InlineKeyboardMarkup(row_width=1)
    
    # --- [ القائمة الرئيسية ] ---
    if not expand_section:
        markup.add(
            InlineKeyboardButton("✂️ إغلاق جزئي", callback_data=f"exp_cl_{u_id}_{t_id}"),
            InlineKeyboardButton("🎯 أهداف الربح والخسارة", callback_data=f"exp_risk_{u_id}_{t_id}"),
            InlineKeyboardButton("🔙 العودة", callback_data=f"active_trades_view:{u_id}")
        )
    
    # --- [ قسم الإغلاق الجزئي ] ---
    elif expand_section == "cl":
        text += "\n<b>💡 اختر نسبة الإغلاق من حجم العقد:</b>"
        btns = [InlineKeyboardButton(f"{p}%", callback_data=f"conf_cl_{p}_{u_id}_{t_id}") for p in [10, 25, 50, 75]]
        markup.row(*btns)
        markup.add(InlineKeyboardButton("🛑 إغلاق 100% (تأكيد)", callback_data=f"conf_cl_100_{u_id}_{t_id}"))
        markup.add(InlineKeyboardButton("🔙 رجوع", callback_data=f"manage_trade:{t_id}"))

    # --- [ قسم إدارة المخاطر SL/TP ] ---
    elif expand_section == "risk":
        side = trade['side']
        lev = float(trade['leverage'])
        qty = float(trade.get('quantity', 0)) 
        # جلب صافي الربح الحالي/الخسارة للحساب (إذا لم يتوفر نضع 100 كافتراضي)
        net_balance = float(trade.get('margin', 100)) 
        
        text += f"\n<b>⚙️ نظام إدارة المخاطر الذكي:</b>\n"
        text += f"• الرافعة: {int(lev)}x | الدخول: {fmt(entry)}\n"

        def get_price_by_pnl(amount_to_lose_or_gain, is_profit=False):
            if qty <= 0: return entry
            price_change = amount_to_lose_or_gain / (qty * (lev / lev)) # معادلة بسيطة للتغير
            # تحسين الحساب بناءً على الرافعة والكمية
            move_needed = (amount_to_lose_or_gain / (margin * lev)) * entry
            
            if side == "LONG":
                res = entry + move_needed if is_profit else entry - move_needed
            else:
                res = entry - move_needed if is_profit else entry + move_needed
            return res

        # --- توليد مستويات وقف الخسارة (SL) ---
        text += "\n<b>🛑 مستويات وقف الخسارة المقترحة:</b>"
        is_in_profit = (side == "LONG" and c_price > entry) or (side == "SHORT" and c_price < entry)
        
        targets = []
        if is_in_profit:
            # إذا كان رابحاً: خيارات تأمين الربح
            targets = [
                (entry, "الدخول (BE)"),
                (calc_price(entry, 10, True, side, lev), "+10%"), 
                (calc_price(entry, 25, True, side, lev), "+25%"), 
                (calc_price(c_price, 5, False, side, lev), "Trailing 5%")
            ]
        else:
            # إذا كان خاسراً: مستويات وقف خسارة من الهامش
            for p in [20, 40, 60, 80]:
                targets.append((calc_price(entry, p, False, side, lev), f"SL {p}%"))

        # بناء الأزرار (مع فحص التصفية)
        row = []
        for opt_price, label in targets:
            # التحقق أن الـ SL ليس خلف التصفية
            valid = (side == "LONG" and opt_price > liq) or (side == "SHORT" and opt_price < liq)
            if valid or is_in_profit:
                btn_label = f"{label} ({fmt(opt_price)})"
                # نرسل السعر خام في الـ callback ليعالجه الـ handler بدقة
                row.append(InlineKeyboardButton(btn_label, callback_data=f"pr_sl_{u_id}_{t_id}_{opt_price:.6f}"))
                if len(row) == 2:
                    markup.row(*row)
                    row = []
        if row: markup.row(*row)

        # --- توليد أهداف جني الأرباح (TP) ---
        text += "\n\n<b>💰 أهداف جني الأرباح (ROE):</b>"
        tp_levels = [(50, "M1"), (100, "M2"), (200, "M3"), (500, "L1"), (1000, "L2")]
        
        tp_row = []
        for roe, lab in tp_levels:
            target_p = calc_price(entry, roe, True, side, lev)
            tp_row.append(InlineKeyboardButton(f"{lab} +{roe}% ({fmt(target_p)})", callback_data=f"pr_tp_{u_id}_{t_id}_{target_p:.6f}"))
            if len(tp_row) == 2:
                markup.row(*tp_row)
                tp_row = []
        if tp_row: markup.row(*tp_row)

        markup.add(InlineKeyboardButton("🔙 رجوع للإعدادات", callback_data=f"manage_trade:{t_id}"))

    return text, markup

async def close_trade_manually(trade_id, current_price):
    """إغلاق الصفقة وتصفية الحساب وإرجاع الرصيد للبنك بدقة float"""
    try:
        # 1. جلب بيانات الصفقة
        res = supabase.table("active_trades").select("*").eq("trade_id", str(trade_id)).execute()
        if not res.data: 
            return False, "⚠️ الصفقة غير موجودة أو تم إغلاقها مسبقاً."
        
        trade = res.data[0]
        user_id = int(trade['user_id'])
        
        # 🟢 استخدام float لضمان دقة العملات والكميات
        entry = float(trade['entry_price'])
        margin = float(trade['margin'])
        lev = float(trade['leverage'])
        side = trade['side']
        cur_price = float(current_price) 
        
        # 2. حساب الربح/الخسارة (PNL)
        if entry > 0:
            pnl_pct = (cur_price - entry) / entry if side == 'LONG' else (entry - cur_price) / entry
        else:
            pnl_pct = 0.0
            
        # الربح الفعلي = الهامش * نسبة التغير * الرافعة
        pnl_amount = margin * pnl_pct * lev
        total_return = margin + pnl_amount 
        
        # 🛡️ حماية التصفية: لا يمكن خسارة أكثر من الهامش الموضوع
        if total_return < 0: 
            total_return = 0.0 
        
        # 3. تحديث رصيد البنك (إضافة الهامش + الربح/الخسارة)
        user_res = supabase.table("users_global_profile").select("bank_balance").eq("user_id", user_id).execute()
        if user_res.data:
            current_bank = float(user_res.data[0]['bank_balance'])
            new_bank = max(0.0, current_bank + total_return) # ضمان عدم نزول البنك تحت الصفر
            
            supabase.table("users_global_profile").update({
                "bank_balance": new_bank
            }).eq("user_id", user_id).execute()
        
        # 4. تجميد الصفقة (إيقاف النشاط)
        # ملاحظة: تم الاكتفاء بـ is_active لعدم وجود أعمدة pnl/close_price في جدولك حالياً
        supabase.table("active_trades").update({
            "is_active": False
        }).eq("trade_id", str(trade_id)).execute()
        
        return True, pnl_amount

    except Exception as e:
        import logging
        logging.error(f"Error in close_trade_manually: {e}")
        return False, "❌ حدث خطأ فني أثناء تصفية الصفقة."
         
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

# --- [ 1. دالة الكيبورد التفاعلي للفريمات ] ---
def get_coin_keyboard(user_id, symbol, current_tf="15m"):
    markup = InlineKeyboardMarkup(row_width=5)
    
    # صف الفريمات (تحديد الفريم النشط)
    tfs = ['15m', '1h', '2h', '4h', '1d']
    tf_buttons = []
    for tf in tfs:
        text = f"🔘 {tf}" if tf == current_tf else tf
        tf_buttons.append(InlineKeyboardButton(text, callback_data=f"coin_view:{user_id}:{symbol}:{tf}"))
    markup.row(*tf_buttons)
    
    # صف توصية VIP
    markup.row(InlineKeyboardButton("💎 تـوصـيـة VIP حـصـريـة 💎", callback_data=f"vip_signal:{user_id}:{symbol}"))
    
    # صف الأوامر السريعة
    markup.row(
        InlineKeyboardButton("🟢 شـراء (LONG)", callback_data=f"setup_trade:{user_id}:{symbol}:LONG"),
        InlineKeyboardButton("🔴 بـيـع (SHORT)", callback_data=f"setup_trade:{user_id}:{symbol}:SHORT")
    )
    
    # زر الرجوع المخصص
    markup.row(InlineKeyboardButton("🔙 رجـوع", callback_data=f"market_tab:{user_id}:trending"))
    return markup

def get_trade_setup_keyboard(user_id):
    session = trade_sessions.get(user_id)
    if not session: return None
    
    sym = session['symbol']
    side = session['side']
    show_zones = session.get('show_zones', False) # هل عرضنا مناطق الدخول؟
    selected_price = session.get('selected_entry_price', None)

    markup = InlineKeyboardMarkup(row_width=3)
    
    # صف الرافعة والنسبة
    markup.row(
        InlineKeyboardButton(f"⚖️ {session['leverage']}x", callback_data=f"trade_cycle:{user_id}:leverage"),
        InlineKeyboardButton(f"💼 {session['margin_pct']}%", callback_data=f"trade_cycle:{user_id}:margin")
    )
    
    # زر مناطق الدخول (يتحول عند الضغط)
    if not show_zones:
        markup.add(InlineKeyboardButton("🎯 تحديد منطقة الدخول", callback_data=f"trade_zones:{user_id}:show"))
    else:
        # توليد مناطق الدخول
        c_price = session['market_price']
        high = session['high_24h']
        low = session['low_24h']
        
        # حساب 4 مناطق دخول منطقية
        zones = []
        if side == 'LONG':
            # مناطق بين الأدنى والسعر الحالي
            zones = np.linspace(low, c_price, 4).tolist()
        else:
            # مناطق بين الحالي والأعلى
            zones = np.linspace(c_price, high, 4).tolist()
        
        zone_buttons = []
        for z in zones:
            is_sel = "✅" if selected_price and abs(selected_price - z) < 0.0001 else ""
            txt = f"{is_sel} {z:,.4f}" if z < 1 else f"{is_sel} {z:,.2f}"
            zone_buttons.append(InlineKeyboardButton(txt, callback_data=f"set_zone:{user_id}:{z}"))
        
        markup.row(*zone_buttons[:2])
        markup.row(*zone_buttons[2:])
        markup.add(InlineKeyboardButton("⚡ العودة للسعر المباشر (Market)", callback_data=f"set_zone:{user_id}:market"))

    # زر التأكيد والإلغاء
    confirm_text = "🚀 تأكيد الشراء" if side == 'LONG' else "🩸 تأكيد البيع"
    markup.add(InlineKeyboardButton(confirm_text, callback_data=f"trade_confirm:{user_id}:{sym}"))
    markup.add(InlineKeyboardButton("❌ إلغاء", callback_data=f"coin_view:{user_id}:{sym}"))
    
    return markup
    

async def update_trade_ui(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in trade_sessions: return
    
    session = trade_sessions[user_id]
    sym = session['symbol']
    side = session['side']
    
    # نستخدم حلقة لتحديث البيانات تلقائياً (مثلاً 15 مرة كل 3 ثوانٍ)
    for _ in range(15):
        if user_id not in trade_sessions: break # خروج إذا أغلق المستخدم الجلسة
        
        session = trade_sessions[user_id]
        
        # 1. جلب السعر اللحظي من سوبابيس (للحسابات المباشرة)
        res = supabase.table("crypto_market_simulation").select("*").eq("symbol", sym).execute()
        if not res.data: break
        
        coin_data = res.data[0]
        market_price = float(coin_data['current_price'])
        
        # حفظ السعر الحالي في الجلسة للمقارنة
        session['market_price'] = market_price
        
        # إذا لم يختر المستخدم سعر دخول محدد (Limit)، نستخدم سعر السوق
        if session.get('selected_entry_price') is None:
            price = market_price
            session['entry_price'] = price
            status_tag = "⚡ سـعر الـسوق (مباشر)"
        else:
            price = session['selected_entry_price']
            status_tag = "🕒 سـعر معلق (Limit)"

        # 2. الحسابات المالية الدقيقة
        bal = session['balance']
        lev = session['leverage']
        pct = session['margin_pct']
        
        margin_amount = bal * (pct / 100.0)
        total_position = margin_amount * lev
        quantity = total_position / price if price > 0 else 0
        
        # حساب التصفية بناءً على السعر (المعلق أو المباشر)
        liq_price = calculate_liquidation(price, lev, side, margin_amount=margin_amount, quantity=quantity)
        
        side_text = "🟢 شـراء (LONG) 🚀" if side == 'LONG' else "🔴 بـيـع (SHORT) 🩸"
        
        # 3. بناء النص التفاعلي
        text = f"⚙️ | <b>إعـداد صـفـقـة: #{sym}</b>\n"
        text += f"الـنوع: {side_text} | {status_tag}\n"
        text += "━━━━━━━━━━━━━━━━━━\n"
        
        # تمييز السعر إذا كان يتحدث تلقائياً
        p_str = f"{price:,.4f}" if price < 1 else f"{price:,.2f}"
        text += f"💵 سـعـر الـدخول: <code>{p_str} $</code> {'🔄' if status_tag.startswith('⚡') else '📌'}\n"
        
        text += f"🏦 رصـيـدك الـمـتـاح: <code>{bal:,.2f} $</code>\n\n"
        text += f"⚖️ الـرافـعـة الـمـالـيـة: <b>{lev}x</b>\n"
        text += f"💼 الـمـبـلـغ الـمـسـتـخـدم: <b>{margin_amount:,.2f} $</b> ({pct}%)\n"
        text += f"🪙 حـجـم الـعـمـلات: <b>{quantity:,.4f} {sym}</b>\n"
        
        text += f"\n⚠️ <b>سـعر الـتصفية:</b> <code>{liq_price:,.4f} $</code>\n"
        text += "━━━━━━━━━━━━━━━━━━\n"
        text += "<i>البيانات تتحدث تلقائياً.. اختر إعداداتك ثم أكد.</i>"

        try:
            # تحديث الرسالة والكيبورد (الذي يحتوي الآن على مناطق الدخول)
            await callback_query.message.edit_text(
                text, 
                reply_markup=get_trade_setup_keyboard(user_id), 
                parse_mode="HTML"
            )
        except:
            # لتجنب أخطاء تليجرام في حال لم يتغير النص أو تم مسح الرسالة
            pass

        # إذا كان المستخدم اختار سعراً معلقاً، لا داعي للتحديث اللحظي للسعر (اختياري)
        if status_tag.startswith('🕒'): 
            break 
            
        await asyncio.sleep(3) # انتظر 3 ثوانٍ قبل التحديث القادم
        
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
        # تم حذف الشرطة السفلية _ قبل النقطتين : لتطابق المعالج
        InlineKeyboardButton("📋 صفقاتي", callback_data=f"active_trades_view:{user_id}"),
        InlineKeyboardButton("🛒 السوق", callback_data=f"market_tab:{user_id}:trending")
    )
    return markup
    

def get_trades_keyboard(user_id, trades):
    markup = InlineKeyboardMarkup(row_width=1) 
    for trade in trades:
        # تحويل المعرف لسلسلة نصية
        t_id_str = str(trade.get('trade_id'))
        symbol = trade.get('symbol', 'COIN')
        
        # 1. زر إعدادات الصفقة (للتعديل على SL/TP)
        # 2. زر عرض الشارت (ينقله لواجهة التحليل coin_view)
        markup.row(
            InlineKeyboardButton(f"⚙️ إعدادات {symbol}", callback_data=f"manage_trade:{t_id_str}"),
            InlineKeyboardButton(f"📊 عرض الشارت", callback_data=f"coin_view:{user_id}:{symbol}")
        )        
        
    # أزرار التنقل الإضافية
    markup.add(InlineKeyboardButton("⏳ الطلبات المعلقة", callback_data=f"pending_trades_view:{user_id}"))
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
            return # التعامل مع الخطأ كما في كودك السابق

        bank_bal = float(data.get('bank_balance', 0.0))    # الكاش المتاح حالياً
        wallet_bal = float(data.get('wallet', 0.0))        # المحفظة الرئيسية (خارج التداول)
        debt = float(data.get('debt_balance', 0.0))
        flag = data.get('country_flag', '🇾🇪')

        # 2. تحليل الصفقات النشطة
        trades_res = supabase.table("active_trades").select("*").eq("user_id", user_id).eq("is_active", True).execute()
        active_trades = trades_res.data if trades_res.data else []
        
        long_count = 0
        short_count = 0
        total_locked_margin = 0.0  # المبالغ المستخدمة في الصفقات
        unrealized_pnl = 0.0       # الأرباح والخسائر العائمة

        for trade in active_trades:
            if trade['side'] == 'LONG': long_count += 1
            else: short_count += 1
            
            margin = float(trade['margin'])
            total_locked_margin += margin
            
            # حساب الـ PnL (نفس منطقك السابق)
            symbol = trade['symbol']
            coin_res = supabase.table("crypto_market_simulation").select("current_price").eq("symbol", symbol).execute()
            if coin_res.data:
                current_price = float(coin_res.data[0]['current_price'])
                entry = float(trade['entry_price'])
                lev = float(trade['leverage'])
                if entry > 0:
                    pnl_pct = (current_price - entry) / entry if trade['side'] == 'LONG' else (entry - current_price) / entry
                    unrealized_pnl += (margin * pnl_pct * lev)

        # 🎯 3. الحسبة اللي طلبتها يا أثر:
        # إجمالي رصيد التداول (الرأس مال الكلي) = الكاش المتاح + المبالغ المستخدمة
        total_trading_balance = bank_bal + total_locked_margin
        
        # صافي القيمة (السيولة الفعلية مع الأرباح)
        equity = total_trading_balance + unrealized_pnl
        
        pnl_color = "🟢" if unrealized_pnl >= 0 else "🔴"

        # 4. التنسيق بستايل بينانس (إظهار الجمع)
        text = (
            f"🏦 | <b>مـركـز إدارة الأصـول</b>\n"
            f"   ━━━━━━━━━━━━━━━━━━\n"
            f"👤 الـمـستخدم: <b>{first_name}</b> {flag}\n\n"
            f"💳 <b>إجمالي الرصيد (Total):</b> <code>{total_trading_balance:,.2f} $</code>\n"
            f"   <i>(كاش: {bank_bal:,.2f} + مستخدم: {total_locked_margin:,.2f})</i>\n\n"
            f"💎 <b>صافي القيمة (Equity):</b> <code>{equity:,.2f} $</code>\n"
            f"💰 <b>المحفظة الفورية:</b> <code>{wallet_bal:,.2f} $</code>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>إحصائيات المـراكز:</b>\n"
            f"🟢 شراء: <b>{long_count}</b> | 🔴 بيع: <b>{short_count}</b>\n"
            f"{pnl_color} <b>الأرباح العائمة:</b> <b>{unrealized_pnl:+.2f} $</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
        )
        
        if debt > 0:
            # إذا وصلت السيولة (Equity) للصفر، الديون ستظل موجودة لكن الحساب سيتجمد
            text += f"⚠️ <b>الـديون الـمستحقة:</b> <code>{debt:,.2f} $</code>\n"
        else:
            text += "✅ <b>حالة الائتمان:</b> ممتاز (لا يوجد دين)\n"
        
        text += "   ━━━━━━━━━━━━━━━━━━"

        # 6. استدعاء الكيبورد وتحديث الواجهة
        # نمرر قيمة debt للكيبورد لكي تظهر أزرار "تسديد الدين" إذا كان هناك دين
        markup = get_wallet_keyboard(user_id, debt)

        if message:
            await message.answer(text, reply_markup=markup, parse_mode="HTML")
        elif callback:
            # تعديل النص في الرسالة الحالية (تحديث لحظي للسعر)
            try:
                await callback.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
            except Exception:
                # لتجنب خطأ "Message is not modified" إذا لم يتغير السعر
                pass

    except Exception as e:
        import logging
        logging.error(f"❌ Wallet Error for user {user_id}: {e}")
        if message: 
            await message.answer("⚠️ فشل في تحديث بيانات المحفظة.")

 # ==========================================
# --- [ مستمع السوق ] ---
# ==========================================            
@dp.message_handler(Text(equals=["تداول", "السوق", "التداول"], ignore_case=True))
async def listener_market(message: types.Message):
    user_id = message.from_user.id
    
    # جلب العملات من السوق (Binance Mode)
    res = supabase.table("crypto_market_simulation").select("*").order("volume_24h", desc=True).limit(25).execute()
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
    # 🔐 القفل الأمني: التأكد أن من ضغط على الزر هو نفسه صاحب الطلب
    data_parts = callback_query.data.split(':')
    owner_id = int(data_parts[1]) # الـ ID المخزن في الزر
    visitor_id = callback_query.from_user.id # الـ ID للشخص الذي ضغط الآن

    if visitor_id != owner_id:
        return await callback_query.answer("⚠️ هذه القائمة ليست لك! اطلب قائمة السوق الخاصة بك من محفظتك.", show_alert=True)

    if not await is_authorized(callback_query): return
    
    try:
        tab_type = data_parts[2]
        
        # جلب البيانات بناءً على التبويب
        if tab_type == 'gainers':
            res = supabase.table("crypto_market_simulation").select("*").order("change_24h", desc=True).limit(25).execute()
            header = "📈 <b>الأعلى ربحاً (24h):</b>"
        elif tab_type == 'losers':
            res = supabase.table("crypto_market_simulation").select("*").order("change_24h", desc=False).limit(25).execute()
            header = "📉 <b>الأكثر خسارة (24h):</b>"
        else: # trending
            res = supabase.table("crypto_market_simulation").select("*").order("volume_24h", desc=True).limit(25).execute()
            header = "🔥 <b>الأكثر رواجاً (السيولة):</b>"
            
        if not res.data:
            return await callback_query.answer("⚠️ لا توجد بيانات حالياً.", show_alert=True)

        text = f"📊 | <b>سـوق الـعـمـلات (Binance Mode)</b>\n━━━━━━━━━━━━━━━━━━\n{header}\n\n"
        markup = InlineKeyboardMarkup(row_width=2)
        
        for c in res.data:
            sym = c['symbol'].replace("USDT", "")
            price = float(c.get('current_price', 0))
            chg = float(c.get('change_24h', 0))
            
            icon = "🟢" if chg >= 0 else "🔴"
            price_format = f"{price:,.4f}" if price < 1 else f"{price:,.2f}"
            
            text += f"{icon} <b>{sym}</b> : <code>{price_format}$</code> ({chg:+.2f}%)\n"
            
            # نمرر owner_id لضمان استمرارية الحماية في الصفحات القادمة
            markup.insert(InlineKeyboardButton(f"🪙 {sym}", callback_data=f"coin_view:{owner_id}:{c['symbol']}"))

        # أزرار التبويبات (كلها تحمل ID المالك)
        markup.row(
            InlineKeyboardButton("🔥 الرائجة", callback_data=f"market_tab:{owner_id}:trending"),
            InlineKeyboardButton("📈 الرابحة", callback_data=f"market_tab:{owner_id}:gainers"),
            InlineKeyboardButton("📉 الخاسرة", callback_data=f"market_tab:{owner_id}:losers")
        )
        markup.add(InlineKeyboardButton("🔙 عودة للمحفظة", callback_data=f"wallet_view:{owner_id}"))
        
        await callback_query.message.edit_text(text, reply_markup=markup, parse_mode="HTML")

    except Exception as e:
        logging.error(f"Error in market_tab: {e}")
        await callback_query.answer("⚠️ فشل تحديث بيانات السوق.", show_alert=True)
        
# --- 3. الكولباك (الذي لا يستجيب للضغط + حماية وتنظيف) --
@dp.callback_query_handler(Text(startswith='active_trades_view:'), state="*")
async def callback_view_trades(callback_query: types.CallbackQuery):
    await callback_query.answer()
    
    # تفكيك البيانات باستخدام النقطتين :
    # البيانات المتوقعة: active_trades_view:123456
    data = callback_query.data.split(':') 
    user_id = int(data[1]) # الآيدي سيكون في الخانة الثانية [1]
    
    # 🛡️ الجدار الناري
    if callback_query.from_user.id != user_id:
        return await callback_query.answer("⚠️ ليس لديك صلاحية للوصول إلى لوحة غيرك!", show_alert=True)
    

    try:
        trades, text = await get_active_trades_report(user_id)
        
        # دالة حذف الرسالة في الخلفية
        async def delete_message_later(msg, delay=600):
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
        asyncio.create_task(delete_message_later(callback_query.message, 600))
        
    except Exception as e:
        logging.error(f"Callback View Error: {e}")
        await callback_query.message.answer(f"❌ فشل عرض الصفقات.")
        
        

# --- [ 2. هاندلر عرض الشارت التفاعلي ] ---
@dp.callback_query_handler(Text(startswith='coin_view:'), state="*")
async def process_coin_view(callback_query: types.CallbackQuery):
    try:
        data_parts = callback_query.data.split(':')
        owner_id = int(data_parts[1])
        symbol = data_parts[2]
        
        # التقاط الفريم، وإذا لم يكن موجوداً نعتبره 15m (للتوافق مع القديم)
        tf = data_parts[3] if len(data_parts) > 3 else "15m"
        visitor_id = callback_query.from_user.id

        if visitor_id != owner_id:
            return await callback_query.answer("⚠️ هذه البيانات ليست لك!", show_alert=True)

        # جلب البيانات
        res = supabase.table("crypto_market_simulation").select("*").eq("symbol", symbol).execute()
        if not res.data:
            return await callback_query.answer("⚠️ العملة غير موجودة!", show_alert=True)
            
        coin = res.data[0]
        
        # استخراج البيانات الأساسية
        price = float(coin.get('current_price', 0))
        high = float(coin.get('high_24h', 0))
        low = float(coin.get('low_24h', 0))
        change = float(coin.get('change_24h', 0))
        
        # استخراج مؤشرات الفريم المحدد
        ema20 = float(coin.get(f'ema_20_{tf}', price))
        ema50 = float(coin.get(f'ema_50_{tf}', price))
        ema100 = float(coin.get(f'ema_100_{tf}', price))
        bb_up = float(coin.get(f'bb_upper_{tf}', price))
        bb_mid = float(coin.get(f'bb_middle_{tf}', price))
        bb_low = float(coin.get(f'bb_lower_{tf}', price))
        rsi = float(coin.get(f'rsi_{tf}', 50))
        vol_ma = float(coin.get(f'volume_ma_{tf}', 0))
        vol_current = float(coin.get('volume_24h', 0)) # سيولة 24 ساعة للتبسيط

        def f_num(val): return f"{val:,.4f}" if val < 1 else f"{val:,.2f}"

        # 🧠 [ خوارزمية الترتيب الديناميكي للشارت ] 🧠
        chart_elements = [
            {"name": "البولنجر العلوي", "val": bb_up, "icon": "🟡"},
            {"name": "البولنجر الأوسط", "val": bb_mid, "icon": "⚪"},
            {"name": "البولنجر السفلي", "val": bb_low, "icon": "🟡"},
            {"name": "خط EMA 100", "val": ema100, "icon": "🔵"},
            {"name": "خط EMA 50", "val": ema50, "icon": "🟢"},
            {"name": "خط EMA 20", "val": ema20, "icon": "🔴"},
            {"name": "سعر العملة الحالي", "val": price, "icon": "💵"}
        ]
        # ترتيب العناصر من الأعلى سعراً إلى الأدنى (محاكاة الشارت)
        chart_elements.sort(key=lambda x: x["val"], reverse=True)

        # 📊 [ ترتيب مؤشر الـ RSI ]
        rsi_text = "====================\n📈: مؤشر RSI 14\n"
        if rsi >= 78:
            rsi_text += f"📈: <b>{rsi:.1f}</b> (🔥 تشبع شرائي)\n®: خط علوي 78\n®: خط سفلي 22\n"
        elif rsi <= 22:
            rsi_text += f"®: خط علوي 78\n®: خط سفلي 22\n📈: <b>{rsi:.1f}</b> (❄️ تشبع بيعي)\n"
        else:
            rsi_text += f"®: خط علوي 78\n📈: <b>{rsi:.1f}</b> (في المنتصف)\n®: خط سفلي 22\n"

        # 💧 [ الفوليوم ]
        vol_color = "🟩" if change >= 0 else "🟥"

        # 📝 [ بناء الرسالة النهائية بناءً على قالبك ]
        text = f"<b>{symbol.replace('USDT', '')} / USDT</b> | ⏱ {tf}\n"
        text += f"السعر الحالي: <code>{f_num(price)}</code> ({change:+.2f}%)\n"
        text += f"أعلى سعر: <code>{f_num(high)}</code>\n"
        text += f"أدنى سعر: <code>{f_num(low)}</code>\n"
        text += "----------------------\n"
        
        for el in chart_elements:
            text += f"{el['icon']}: {el['name']} {{ <code>{f_num(el['val'])}</code> }}\n"
            
        text += "----------------------\n"
        text += rsi_text
        text += "====================\n"
        text += f"📈: مؤشر الفوليوم (MA)\n"
        text += f"📈: حجم المتوسط {{ <code>{vol_ma:,.0f}</code> }}\n"
        text += "====================\n"
        text += f"📊: أعمدة السيولة الحالية\n"
        text += f"📊: الحجم {vol_color} {{ <code>{vol_current:,.0f}</code> }}\n"
        text += "===================="

        await callback_query.message.edit_text(
            text, 
            reply_markup=get_coin_keyboard(owner_id, symbol, tf), 
            parse_mode="HTML"
        )
        await callback_query.answer()
    except Exception as e:
        print(f"Error: {e}")
        await callback_query.answer("❌ حدث خطأ في معالجة الشارت.")

# --- [ 3. هاندلر توصية VIP (قالب العنود / الدخول الهجومي) ] ---
@dp.callback_query_handler(Text(startswith='vip_signal:'), state="*")
async def process_vip_signal(callback_query: types.CallbackQuery):
    try:
        data_parts = callback_query.data.split(':')
        owner_id = int(data_parts[1])
        symbol = data_parts[2]

        if callback_query.from_user.id != owner_id:
            return await callback_query.answer("⚠️ لا تملك صلاحية!", show_alert=True)

        res = supabase.table("crypto_market_simulation").select("*").eq("symbol", symbol).execute()
        if not res.data: return
        
        c = res.data[0]
        price = float(c['current_price'])
        
        # تحليل الاتجاه عبر اليومي (1d) والدخول السريع (1h)
        ema50_1d = float(c.get('ema_50_1d', price))
        ema20_1h = float(c.get('ema_20_1h', price))
        rsi_1h = float(c.get('rsi_1h', 50))
        
        # استراتيجية أثر: (قنص الارتدادات الدخول الهجومي)
        if price > ema50_1d and rsi_1h < 78:
            # شراء (LONG)
            direction_text = "شراء (LONG) 🚀"
            entry_1 = ema20_1h # دخول هجومي على EMA 20
            entry_2 = entry_1 * 0.985 # النطاق
            dca = entry_1 * 0.96 # DCA
            sl = dca * 0.98 # وقف صارم
            tp1 = price * 1.02
            tp2 = price * 1.04
            tp3 = price * 1.08
            emoji_trend = "📈"
        else:
            # بيع (SHORT)
            direction_text = "بيع (SHORT) 📉"
            entry_1 = ema20_1h
            entry_2 = entry_1 * 1.015
            dca = entry_1 * 1.04
            sl = dca * 1.02
            tp1 = price * 0.98
            tp2 = price * 0.96
            tp3 = price * 0.92
            emoji_trend = "📉"

        def f_num(val): return f"{val:,.4f}" if val < 1 else f"{val:,.2f}"

        # القالب الاحترافي الخاص بك (الجاهز للنسخ)
        signal_text = f"🔥 <b>توصية VIP مبنية على استراتيجية قنص الارتدادات</b>\n\n"
        signal_text += f"{direction_text}: #{symbol}\n"
        signal_text += f"نطاق الدخول: <code>{f_num(entry_1)}</code> - <code>{f_num(entry_2)}</code>\n"
        signal_text += f"تعديل المتوسط (DCA): <code>{f_num(dca)}</code>\n"
        signal_text += f"وقف الخسارة: <code>{f_num(sl)}</code>\n\n"
        signal_text += f"الأهداف:\n"
        signal_text += f"👈 الهدف الأول: <code>{f_num(tp1)}</code>\n"
        signal_text += f"👈 الهدف الثاني: <code>{f_num(tp2)}</code>\n"
        signal_text += f"👈 الهدف الثالث: <code>{f_num(tp3)}</code>\n\n"
        signal_text += f"اضغط أدناه وافتح صفقة {direction_text.split(' ')[0]} {emoji_trend}"

        # زر الرجوع فقط للتوصية
        back_kb = InlineKeyboardMarkup().add(InlineKeyboardButton("🔙 رجوع للشارت", callback_data=f"coin_view:{owner_id}:{symbol}:15m"))

        await callback_query.message.edit_text(signal_text, reply_markup=back_kb, parse_mode="HTML")
        await callback_query.answer("تم اصدار التوصية بنجاح 💎")

    except Exception as e:
        print(f"VIP Error: {e}")
        await callback_query.answer("❌ تعذر توليد التوصية.")
        

# ==========================================
# 7. معالجات دورة الصفقة (المطورة لدعم الفواصل والأمان)
# ==========================================
@dp.callback_query_handler(Text(startswith='setup_trade:'), state="*")
async def process_setup_trade(callback_query: types.CallbackQuery):
    data_parts = callback_query.data.split(':')
    owner_id = int(data_parts[1])
    if callback_query.from_user.id != owner_id:
        return await callback_query.answer("⚠️ المتصفح ليس لك!", show_alert=True)

    symbol = data_parts[2]
    side = data_parts[3]
    
    try:
        # جلب السعر والمستويات (High/Low)
        coin_res = supabase.table("crypto_market_simulation").select("*").eq("symbol", symbol).execute()
        if not coin_res.data:
            return await callback_query.answer("⚠️ العملة غير متوفرة.", show_alert=True)
            
        coin = coin_res.data[0]
        price = float(coin['current_price'])
        balance = await get_user_bank_balance(owner_id)
        
        # تخزين الجلسة مع إضافة بيانات الـ High و Low ومفتاح للمناطق
        trade_sessions[owner_id] = {
            'symbol': symbol,
            'side': side,
            'market_price': price,        # السعر المباشر
            'entry_price': price,         # السعر المعتمد (قد يتغير لو اختار منطقة)
            'selected_entry_price': None, # لحفظ السعر المختار يدوياً
            'high_24h': float(coin.get('high_24h', price)),
            'low_24h': float(coin.get('low_24h', price)),
            'leverage': 10,
            'margin_pct': 25,
            'balance': float(balance),
            'show_zones': False           # لإظهار/إخفاء أزرار المناطق
        }
        
        # حذفنا المدة كما طلبت، وسنبدأ التحديث اللحظي
        await update_trade_ui(callback_query)
        
    except Exception as e:
        print(f"Error: {e}")
        await callback_query.answer("⚠️ خطأ في التجهيز.")
        
@dp.callback_query_handler(Text(startswith='trade_cycle:'), state="*")
async def process_trade_cycle(callback_query: types.CallbackQuery):
    # 🔐 القفل الأمني (قراءة المالك من الداتا)
    data_parts = callback_query.data.split(':')
    owner_id = int(data_parts[1])
    if callback_query.from_user.id != owner_id:
        return await callback_query.answer("⚠️ المتصفح ليس لك!", show_alert=True)

    if owner_id not in trade_sessions:
        return await callback_query.answer("⚠️ انتهت الجلسة، اطلب العملة مجدداً.", show_alert=True)
    
    action = data_parts[2]
    session = trade_sessions[owner_id]
    
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
    data_parts = callback_query.data.split(':')
    owner_id = int(data_parts[1])
    if callback_query.from_user.id != owner_id:
        return await callback_query.answer("⚠️ لا يمكنك تأكيد صفقة غيرك!", show_alert=True)

    if owner_id not in trade_sessions:
        return await callback_query.answer("⚠️ انتهت الجلسة.", show_alert=True)
        
    session = trade_sessions[owner_id]
    
    # حساب الهامش
    margin_amount = session['balance'] * (session['margin_pct'] / 100.0)
    
    # فحص هل هي صفقة "معلقة" (Limit) أم "فورية" (Market)
    is_limit = session.get('selected_entry_price') is not None
    is_active_status = not is_limit  # إذا كان ليميت تكون False
    
    # السعر المعتمد للتنفيذ
    exec_price = session['entry_price']

    try:
        # الحسابات الدقيقة
        quantity = (margin_amount * session['leverage']) / exec_price
        liq_price = calculate_liquidation(exec_price, session['leverage'], session['side'])
        
        new_balance = session['balance'] - margin_amount
        
        # 1. تحديث الرصيد (يتم خصم المبلغ بمجرد فتح الطلب سواء معلق أو فوري لضمان الجدية)
        supabase.table("users_global_profile").update({
            "bank_balance": float(new_balance) 
        }).eq("user_id", owner_id).execute()
        
        # 2. إدخال البيانات في active_trades
        trade_data = {
            "user_id": owner_id,
            "symbol": session['symbol'],
            "side": session['side'],
            "entry_price": exec_price,
            "leverage": session['leverage'],
            "margin": margin_amount,
            "quantity": quantity,
            "liquidation_price": liq_price,
            "is_active": is_active_status, # التعديل الجوهري هنا ✅
            "created_at": datetime.now().isoformat()
        }
        
        supabase.table("active_trades").insert(trade_data).execute()
        
        # 3. عرض رسالة النجاح
        status_text = "⚡ صفقة فورية نشطة" if is_active_status else "⏳ طلب معلق (Limit)"
        
        text = f"✅ <b>تم تنفيذ العملية بنجاح!</b>\n\n"
        text += f"الحالة: {status_text}\n"
        text += f"العملة: #{session['symbol']}\n"
        text += f"سعر الدخول: <code>{exec_price:,.4f} $</code>\n"
        text += f"المبلغ المحجوز: <code>{margin_amount:,.2f} $</code>\n"
        text += f"الرصيد المتبقي: <code>{new_balance:,.2f} $</code>"
        
        # تنظيف الجلسة
        del trade_sessions[owner_id]
        
        markup = InlineKeyboardMarkup()
        btn_text = "📋 صفقاتي النشطة" if is_active_status else "⏳ طلباتي المعلقة"
        markup.add(InlineKeyboardButton(btn_text, callback_data=f"active_trades_view:{owner_id}"))
        markup.add(InlineKeyboardButton("🔙 العودة للسوق", callback_data=f"market_tab:{owner_id}:trending"))
        
        await callback_query.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
          
    except Exception as e:
        print(f"Trade Confirmation Error: {e}")
        await callback_query.answer("❌ فشل تنفيذ الصفقة.")
        
@dp.callback_query_handler(Text(startswith='pending_trades_view:'), state="*")
async def pending_trades_view(callback_query: types.CallbackQuery):
    data_parts = callback_query.data.split(':')
    owner_id = int(data_parts[1])
    
    # التأكد من هوية المستخدم
    if callback_query.from_user.id != owner_id:
        return await callback_query.answer("⚠️ هذه القائمة ليست لك!", show_alert=True)

    try:
        # جلب الصفقات غير النشطة (is_active = False)
        res = supabase.table("active_trades")\
            .select("*")\
            .eq("user_id", owner_id)\
            .eq("is_active", False)\
            .order("created_at", desc=True).execute()
        
        trades = res.data
        
        if not trades:
            text = "⏳ <b>لا توجد لديك طلبات معلقة حالياً.</b>\n\n<i>بمجرد تحديد منطقة دخول، ستظهر طلباتك هنا حتى يلمسها السعر.</i>"
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("📋 عرض الصفقات النشطة", callback_data=f"active_trades_view:{owner_id}"))
            markup.add(InlineKeyboardButton("🔙 العودة للمحفظة", callback_data=f"wallet_tab:{owner_id}"))
            return await callback_query.message.edit_text(text, reply_markup=markup, parse_mode="HTML")

        text = "⏳ <b>قائمة الطلبات المعلقة (Limit Orders)</b>\n"
        text += "━━━━━━━━━━━━━━━━━━\n\n"
        
        markup = InlineKeyboardMarkup(row_width=2)
        
        for t in trades:
            side_icon = "🟢" if t['side'] == 'LONG' else "🔴"
            entry_p = float(t['entry_price'])
            margin = float(t['margin'])
            symbol = t['symbol']
            trade_id = t['trade_id']
            
            p_fmt = f"{entry_p:,.4f}" if entry_p < 1 else f"{entry_p:,.2f}"
            
            text += f"{side_icon} <b>#{symbol}</b> ({t['leverage']}x)\n"
            text += f"🎯 سعر الدخول المطلوب: <code>{p_fmt} $</code>\n"
            text += f"💰 الهامش المحجوز: <code>{margin:,.2f} $</code>\n"
            text += f"🗓 التاريخ: <code>{t['created_at'][:16].replace('T', ' ')}</code>\n"
            text += "━━━━━━━━━━━━━━━━━━\n"
            
            # زر إلغاء الطلب لكل صفقة
            markup.add(InlineKeyboardButton(f"❌ إلغاء طلب {symbol}", callback_data=f"cancel_limit:{owner_id}:{trade_id}"))

        # أزرار التنقل السفلية
        markup.row(
            InlineKeyboardButton("🔙 صفقاتي النشطة", callback_data=f"active_trades_view:{owner_id}"),
            InlineKeyboardButton("🏠 الرئيسية", callback_data=f"wallet_tab:{owner_id}")
        )

        await callback_query.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
        
    except Exception as e:
        print(f"Error viewing pending trades: {e}")
        await callback_query.answer("⚠️ حدث خطأ أثناء جلب البيانات.")
        
@dp.callback_query_handler(Text(startswith='cancel_limit:'), state="*")
async def cancel_limit_order(callback_query: types.CallbackQuery):
    data_parts = callback_query.data.split(':')
    owner_id = int(data_parts[1])
    trade_id = data_parts[2]
    
    if callback_query.from_user.id != owner_id:
        return await callback_query.answer("⚠️ لا يمكنك إلغاء طلب غيرك!", show_alert=True)

    try:
        # 1. جلب بيانات الصفقة للتأكد من مبلغ الهامش (Margin)
        trade_res = supabase.table("active_trades").select("*").eq("trade_id", trade_id).execute()
        if not trade_res.data:
            return await callback_query.answer("⚠️ الطلب غير موجود أو تم تنفيذه بالفعل.", show_alert=True)
            
        trade = trade_res.data[0]
        refund_amount = float(trade['margin'])
        
        # 2. جلب رصيد المستخدم الحالي لإعادة المال
        balance = await get_user_bank_balance(owner_id)
        new_balance = float(balance) + refund_amount
        
        # 3. تنفيذ العمليات (تحديث الرصيد وحذف الصفقة)
        # تحديث الرصيد
        supabase.table("users_global_profile").update({"bank_balance": new_balance}).eq("user_id", owner_id).execute()
        
        # حذف الطلب المعلق
        supabase.table("active_trades").delete().eq("trade_id", trade_id).execute()
        
        await callback_query.answer(f"✅ تم إلغاء الطلب وإعادة {refund_amount:,.2f}$ لمحفظتك.", show_alert=True)
        
        # تحديث القائمة بعد الحذف
        await pending_trades_view(callback_query)
        
    except Exception as e:
        print(f"Cancel Error: {e}")
        await callback_query.answer("❌ فشل إلغاء الطلب.")
        
# ==========================================
# --- [ المعالجات Handlers المحدثة ] ---
# ==========================================

# 1. معالج اختيار الهدف والتأكيد (دعم الفواصل العشرية)
@dp.callback_query_handler(Text(startswith=('pr_sl_', 'pr_tp_')), state="*")
async def handle_automated_risk_selection(callback_query: types.CallbackQuery):
    try:
        data = callback_query.data.split('_') # الهيكلية: pr_sl_uid_tid_price
        risk_type = data[1]
        btn_user_id = int(data[2])
        trade_id = data[3]
        # 🟢 تعديل: تحويل السعر لـ float بدلاً من int لدعم العملات الرخيصة
        target_price = float(data[4]) 

        if callback_query.from_user.id != btn_user_id:
            return await callback_query.answer("⚠️ هذه الصلاحية ليست لك! 🚫", show_alert=True)

        res = supabase.table("active_trades").select("*").eq("trade_id", trade_id).execute()
        if not res.data:
            return await callback_query.answer("⚠️ الصفقة مغلقة.")
        
        trade = res.data[0]
        # 🟢 تعديل: جلب القيم كـ float لضمان دقة الحسابات
        entry = float(trade['entry_price'])
        liq = float(trade['liquidation_price'])
        side = trade['side']
        lev = int(trade['leverage'])
        margin = float(trade['margin'])

        # فحص التصفية (Liquidation Check)
        if risk_type == "sl":
            if (side == "LONG" and target_price <= liq) or (side == "SHORT" and target_price >= liq):
                p_fmt = f"{target_price:,.4f}" if target_price < 1 else f"{target_price:,.2f}"
                return await callback_query.answer(f"⚠️ السعر {p_fmt} خلف التصفية!", show_alert=True)

        # حسابات الربح والخسارة المتوقعة بدقة
        diff = (target_price - entry) if side == "LONG" else (entry - target_price)
        pnl_pct = (diff / entry) * lev * 100
        expected_cash = margin * (pnl_pct / 100)

        label = "إيقاف الخسارة (SL)" if risk_type == "sl" else "جني الأرباح (TP)"
        status_icon = "✅ حماية" if pnl_pct > 0 else "📉 مخاطرة"
        
        # تنسيق السعر للعرض
        p_fmt = f"{target_price:,.4f}" if target_price < 1 else f"{target_price:,.2f}"

        text = f"⚖️ <b>تأكيد مستهدف {label}</b>\n"
        text += f"━━━━━━━━━━━━━━\n"
        text += f"• السعر المختار: <code>{p_fmt} $</code>\n"
        text += f"• الحالة: <b>{status_icon}</b>\n"
        text += f"• النسبة المتوقعة: <b>{pnl_pct:+.2f}%</b>\n"
        text += f"• الربح/الخسارة: <b>{expected_cash:+.2f} $</b>\n\n"
        text += "هل تريد اعتماد هذا المستهدف وحفظه؟"

        # حفظ الكولباك (ملاحظة: تليجرام لده حد 64 بايت، لذا نرسل السعر كما هو)
        save_callback = f"c_{risk_type}_{btn_user_id}_{trade_id}_{data[4]}"
        
        markup = InlineKeyboardMarkup(row_width=1).add(
            InlineKeyboardButton("✅ نعم، تأكيد الحفظ", callback_data=save_callback),
            InlineKeyboardButton("❌ تراجع (العودة)", callback_data=f"exp_risk_{btn_user_id}_{trade_id}")
        )

        await callback_query.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
        await callback_query.answer()

    except Exception as e:
        import logging
        logging.error(f"Error in automated risk: {e}")
        await callback_query.answer("⚠️ خطأ في المعالجة.")

# 2. معالج الحفظ النهائي (دعم numeric)
@dp.callback_query_handler(Text(startswith=('c_sl_', 'c_tp_')), state="*")
async def commit_risk_to_db(callback_query: types.CallbackQuery):
    try:
        data = callback_query.data.split('_')
        risk_type = data[1]
        btn_user_id = int(data[2])
        t_id = data[3]
        # 🟢 تعديل: حفظ السعر كـ float
        new_price = float(data[4]) 

        if callback_query.from_user.id != btn_user_id:
            return await callback_query.answer("⚠️ عذراً، لا تملك الصلاحية! 🚫", show_alert=True)

        column_name = "stop_loss" if risk_type == "sl" else "take_profit"
        label = "وقف الخسارة" if risk_type == "sl" else "جني الأرباح"

        # التحديث في سوبابيس (numeric يقبل float)
        supabase.table("active_trades").update({
            column_name: new_price
        }).eq("trade_id", t_id).execute()
        
        await callback_query.answer(f"✅ تم حفظ {label} بنجاح!", show_alert=True)
        
        # إعادة التوجيه للوحة الإدارة
        callback_query.data = f"manage_trade:{t_id}"
        await callback_manage_trade_handler(callback_query)
        
    except Exception as e:
        import logging
        logging.error(f"Error in commit_risk: {e}")
        await callback_query.answer("❌ خطأ في الحفظ.")

# 3. معالج التوسع (دعم الفواصل في الأسعار الحالية)
@dp.callback_query_handler(Text(startswith='exp_'), state="*")
async def handle_expansion_protected(callback_query: types.CallbackQuery):
    try:
        data = callback_query.data.split('_') 
        section = data[1]
        btn_user_id = int(data[2])
        t_id = data[3]        
        
        if callback_query.from_user.id != btn_user_id:
            return await callback_query.answer("⚠️ مبعسس! هذه الأزرار ليست لك. 🚫", show_alert=True)

        res = supabase.table("active_trades").select("*").eq("trade_id", t_id).execute()
        if not res.data:
            return await callback_query.answer("⚠️ الصفقة غير موجودة.")
        
        trade = res.data[0]
        
        # 🟢 جلب سعر السوق الحالي بالفواصل
        coin_res = supabase.table("crypto_market_simulation").select("current_price").eq("symbol", trade['symbol']).execute()
        current_price = float(coin_res.data[0]['current_price']) if coin_res.data else float(trade['entry_price'])

        # استدعاء دالة العرض (تأكد أن get_trade_settings_view تدعم float)
        text, markup = get_trade_settings_view(trade, current_price, expand_section=section)
        
        await callback_query.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
        await callback_query.answer()

    except Exception as e:
        import logging
        logging.error(f"Expansion Error: {e}")
        await callback_query.answer("❌ حدث خطأ داخلي.")
       

# 4. معالج فتح لوحة الإعدادات (Main Gate)
@dp.callback_query_handler(Text(startswith='manage_trade:'), state="*")
async def callback_manage_trade_handler(callback_query: types.CallbackQuery):
    try:
        t_id = callback_query.data.split(':')[1]
        res = supabase.table("active_trades").select("*").eq("trade_id", t_id).execute()
        
        if not res.data:
            return await callback_query.answer("⚠️ الصفقة غير موجودة أو أغلقت.", show_alert=True)
        
        trade = res.data[0]
        # 🛡️ التأكد من صاحب الصفقة
        if callback_query.from_user.id != int(trade['user_id']):
            return await callback_query.answer("⚠️ لا يمكنك إدارة صفقات الآخرين!", show_alert=True)

        # جلب السعر الحالي بالفواصل العشرية
        coin_res = supabase.table("crypto_market_simulation").select("current_price").eq("symbol", trade['symbol']).execute()
        current_price = float(coin_res.data[0]['current_price']) if coin_res.data else float(trade['entry_price'])

        # إرسال البيانات لدالة العرض (تأكد أن الدالة get_trade_settings_view تقبل float)
        text, markup = get_trade_settings_view(trade, current_price)
        await callback_query.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
        await callback_query.answer()
    except Exception as e:
        logging.error(f"Error in manage_trade: {e}")
        await callback_query.answer("❌ خطأ في فتح الإعدادات.")

 # ==========================================
# --- [ بوابة تأكيد التنفيذ ] ---
# ==========================================
@dp.callback_query_handler(Text(startswith='conf_'), state="*")
async def security_gate_protected(callback_query: types.CallbackQuery):
    try:
        # تفكيك البيانات: conf_action_percent_uid_tid
        _, action, percent, u_id, t_id = callback_query.data.split('_')
        
        if callback_query.from_user.id != int(u_id):
            return await callback_query.answer("⚠️ لا تتدخل في صفقات غيرك! 🚫", show_alert=True)

        res = supabase.table("active_trades").select("symbol").eq("trade_id", t_id).execute()
        if not res.data: 
            return await callback_query.message.edit_text("⚠️ الصفقة مغلقة أو غير موجودة.")
        
        symbol = res.data[0]['symbol']
        act_name = "إغلاق جزء من المركز" if percent != "100" else "إغلاق المركز بالكامل"
        
        text = f"🛡️ <b>تأكيـد التنفيذ: #{symbol}</b>\n"
        text += f"━━━━━━━━━━━━━━━━━━\n"
        text += f"• الإجراء: <b>{act_name}</b>\n"
        text += f"• النسبة: <b>{percent}%</b>\n\n"
        text += "⚠️ <b>سيتم التنفيذ فوراً بسعر السوق الحالي، هل أنت متأكد؟</b>"
        
        markup = InlineKeyboardMarkup(row_width=2).add(
            InlineKeyboardButton("✅ نعم، تنفيذ", callback_data=f"exe_{action}_{percent}_{u_id}_{t_id}"),
            InlineKeyboardButton("❌ تراجع", callback_data=f"manage_trade:{t_id}")
        )
        
        await callback_query.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
        await callback_query.answer()
    except Exception as e:
        logging.error(f"Security Gate Error: {e}")
        await callback_query.answer("❌ خطأ في بوابة التأكيد.")
        

# ==========================================
# --- [ محرك التنفيذ الموحد: الإغلاق فقط ] ---
# ==========================================
@dp.callback_query_handler(Text(startswith='exe_'), state="*")
async def universal_execution_engine(callback_query: types.CallbackQuery):
    try:
        _, action, percent_str, u_id, t_id = callback_query.data.split('_')
        percent = int(percent_str)
        user_id = int(u_id)

        if callback_query.from_user.id != user_id:
            return await callback_query.answer("⚠️ لا تتدخل في صفقات غيرك!", show_alert=True)

        # جلب بيانات المستخدم والصفقة
        account = await get_trading_account_snapshot(user_id)
        res = supabase.table("active_trades").select("*").eq("trade_id", t_id).execute()
        if not res.data: return await callback_query.message.edit_text("❌ الصفقة غير موجودة.")
        
        trade = res.data[0]
        coin_res = supabase.table("crypto_market_simulation").select("current_price").eq("symbol", trade['symbol']).execute()
        # 🟢 استخدام float للسعر الحالي
        cur_price = float(coin_res.data[0]['current_price'])
        
        success_text = ""

        if action == 'cl':
            # حساب الكميات المغلقة بدقة float
            m_to_close = float(trade['margin']) * (percent / 100.0)
            q_to_close = float(trade['quantity']) * (percent / 100.0)
            
            # 🟢 حساب PNL الدقيق
            entry_price = float(trade['entry_price'])
            if trade['side'] == 'LONG':
                pnl_pct = (cur_price - entry_price) / entry_price
            else:
                pnl_pct = (entry_price - cur_price) / entry_price
                
            pnl_amt = m_to_close * pnl_pct * float(trade['leverage'])
            ret_to_bank = m_to_close + pnl_amt

            # تحديث البنك (بدون int لضمان حفظ السنتات)
            new_bank = max(0.0, float(account['free_cash']) + ret_to_bank)
            supabase.table("users_global_profile").update({"bank_balance": new_bank}).eq("user_id", user_id).execute()

            if percent >= 100:
                supabase.table("active_trades").delete().eq("trade_id", t_id).execute()
                success_text = f"✅ <b>تم إغلاق المركز بالكامل: #{trade['symbol']}</b>\n"
            else:
                # تحديث الصفقة (طرح الهامش والكمية المغلقة)
                supabase.table("active_trades").update({
                    "margin": float(trade['margin']) - m_to_close,
                    "quantity": float(trade['quantity']) - q_to_close
                }).eq("trade_id", t_id).execute()
                success_text = f"✂️ <b>تم إغلاق جزئي {percent}%: #{trade['symbol']}</b>\n"

            pnl_emoji = "🟢" if pnl_amt >= 0 else "🔴"
            # تنسيق عرض الأسعار
            e_fmt = f"{entry_price:,.4f}" if entry_price < 1 else f"{entry_price:,.2f}"
            c_fmt = f"{cur_price:,.4f}" if cur_price < 1 else f"{cur_price:,.2f}"
            
            success_text += f"• سعر الدخول: <b>{e_fmt} $</b>\n• سعر الإغلاق: <b>{c_fmt} $</b>\n"
            success_text += f"• الربح/الخسارة: <b>{pnl_amt:+.2f} $</b> {pnl_emoji}\n"
            success_text += f"• العائد للبنك: <b>{ret_to_bank:,.2f} $</b>"

            msg = await callback_query.message.edit_text(success_text, parse_mode="HTML")
            await asyncio.sleep(10)
            try: await msg.delete()
            except: pass

            # تحديث العرض للمستخدم
            trades_left = supabase.table("active_trades").select("trade_id").eq("user_id", user_id).execute()
            if not trades_left.data:
                from bot_handlers import send_main_portfolio
                await send_main_portfolio(callback_query.message, user_id)
            else:
                callback_query.data = f"active_trades_view:{user_id}"
                from bot_handlers import callback_view_trades
                await callback_view_trades(callback_query)

    except Exception as e:
        logging.error(f"Logic Error: {e}")
        await callback_query.answer("❌ حدث خطأ في الحسابات.")
# ==========================================
# 9. زر العودة للوحة التحكم الرئيسية للصفقة (Back Button)
# ==========================================
@dp.callback_query_handler(Text(startswith='back_ts_'), state="*")
async def back_to_settings_protected(callback_query: types.CallbackQuery):
    try:
        data = callback_query.data.split('_') # الهيكلية: back_ts_uid_tid
        btn_user_id = int(data[2])
        t_id = data[3]
        
        if callback_query.from_user.id != btn_user_id:
            return await callback_query.answer("⚠️ الصلاحية منتهية.")

        res = supabase.table("active_trades").select("*").eq("trade_id", t_id).execute()
        if not res.data: 
            return await callback_query.answer("⚠️ الصفقة مغلقة.")
        
        trade = res.data[0]
        coin_res = supabase.table("crypto_market_simulation").select("current_price").eq("symbol", trade['symbol']).execute()
        current_price = int(float(coin_res.data[0]['current_price'])) if coin_res.data else int(float(trade['entry_price']))

        # إرجاع لوحة التحكم الرئيسية بدون توسيع أي قسم
        text, markup = get_trade_settings_view(trade, current_price)
        await callback_query.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
        await callback_query.answer("🔙 تم الرجوع")
        
    except Exception as e:
        import logging
        logging.error(f"Error in Back TS: {e}")
        await callback_query.answer("❌ خطأ في الرجوع للقائمة.")

# ==========================================
# --- [ نظام التحويلات المالية المطور ] ---
# ==========================================

@dp.callback_query_handler(Text(startswith='transfer_flow:'), state="*")
async def transfer_init(callback_query: types.CallbackQuery, state: FSMContext):
    data = callback_query.data.split(':')
    user_id = int(data[1])
    direction = data[2] # to_bank أو to_wallet
    
    # 🔐 القفل الأمني
    if callback_query.from_user.id != user_id:
        return await callback_query.answer("❌ لا يمكنك التحكم بأموال غيرك!", show_alert=True)
    
    await state.update_data(trans_direction=direction)
    await BankTransfer.waiting_for_amount.set()
    
    # رسائل واضحة تدعم مفهوم الكسور
    prompt = "📥 <b>إيداع للتداول</b>\nأرسل المبلغ المراد تحويله (مثال: 10.50):" if direction == "to_bank" else \
             "📤 <b>سحب للمحفظة</b>\nأرسل المبلغ المراد سحبه (مثال: 5.25):"
    
    await callback_query.message.answer(prompt, parse_mode="HTML")
    await callback_query.answer()

# --- [ 2. معالجة المبلغ وتنفيذ التحديث بدقة float ] ---
@dp.message_handler(state=BankTransfer.waiting_for_amount)
async def process_transfer_amount(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    
    # 🟢 تحويل المدخل إلى float لدعم الكسور العشرية
    try:
        # تنظيف النص من أي رموز وإدخاله كـ float
        amount_text = message.text.replace(',', '.').replace('$', '').strip()
        amount = round(float(amount_text), 2) # تقريب لرقمين عشريين (سنتات)
        if amount <= 0: raise ValueError
    except:
        return await message.reply("⚠️ يرجى إرسال مبلغ صحيح (أرقام فقط)، مثال: 10.50")

    state_data = await state.get_data()
    direction = state_data.get('trans_direction')
    
    # جلب بيانات المستخدم (استخدام float للأرصدة)
    user_data = await get_user_data(user_id)
    if not user_data: return await state.finish()

    # 🟢 قراءة الأرصدة كـ float
    wallet_bal = float(user_data.get('wallet', 0) or 0)
    bank_bal = float(user_data.get('bank_balance', 0) or 0)

    try:
        if direction == "to_bank":
            if amount > wallet_bal:
                return await message.reply(f"❌ رصيد المحفظة غير كافٍ.\nالمتاح: <code>{wallet_bal:,.2f} $</code>")
            
            # تحديث سوبابيس (بيانات float متوافقة مع numeric)
            supabase.table("users_global_profile").update({
                "wallet": wallet_bal - amount,
                "bank_balance": bank_bal + amount
            }).eq("user_id", user_id).execute()
            
        else: # سحب للمحفظة
            # فحص الهامش المتاح (Margin Check) إذا كان لديه صفقات مفتوحة
            is_safe, health_msg = await check_financial_health(user_id, amount, "WITHDRAW")
            if not is_safe: return await message.reply(health_msg)
            
            if amount > bank_bal:
                return await message.reply(f"❌ رصيد التداول غير كافٍ.\nالمتاح: <code>{bank_bal:,.2f} $</code>")

            supabase.table("users_global_profile").update({
                "bank_balance": bank_bal - amount,
                "wallet": wallet_bal + amount
            }).eq("user_id", user_id).execute()

        await message.answer(f"✅ تم تحويل <b>{amount:,.2f} $</b> بنجاح!", parse_mode="HTML")
        await state.finish()
        
        # تحديث واجهة المحفظة فوراً
        await process_wallet_logic(user_id, message.from_user.first_name, message=message)

    except Exception as e:
        import logging
        logging.error(f"Transfer DB Error: {e}")
        await message.reply("❌ حدث خطأ أثناء التحديث في قاعدة البيانات.")
        await state.finish()
        
# --- قسم القروض ---
@dp.callback_query_handler(Text(startswith='repay_loan:'), state="*")
async def repay_loan_handler(callback_query: types.CallbackQuery):
    try:
        # 🔐 القفل الأمني
        data_parts = callback_query.data.split(':')
        owner_id = int(data_parts[1])
        if callback_query.from_user.id != owner_id:
            return await callback_query.answer("⚠️ لا يمكنك سداد ديون غيرك!", show_alert=True)

        # جلب البيانات مباشرة (float لدعم الكسور)
        res = supabase.table("users_global_profile").select("bank_balance, debt_balance").eq("user_id", owner_id).execute()
        
        if not res.data:
            return await callback_query.answer("❌ لم يتم العثور على بياناتك.", show_alert=True)
            
        user_data = res.data[0]
        debt = float(user_data.get('debt_balance', 0) or 0)
        bank_bal = float(user_data.get('bank_balance', 0) or 0)
        
        if debt <= 0:
            return await callback_query.answer("✅ ليس لديك أي ديون مستحقة حالياً!", show_alert=True)
            
        if bank_bal < debt:
            missing = debt - bank_bal
            return await callback_query.answer(f"❌ رصيد التداول ({bank_bal:,.2f}$) غير كافٍ.\nتحتاج لجمع {missing:,.2f}$ إضافية للسداد.", show_alert=True)
        
        # تنفيذ عملية الخصم (دقة float)
        new_bank_balance = bank_bal - debt
        
        supabase.table("users_global_profile").update({
            "bank_balance": float(new_bank_balance),
            "debt_balance": 0.0
        }).eq("user_id", owner_id).execute()
        
        await callback_query.answer(f"✅ تم سداد القرض بالكامل ({debt:,.2f}$).\nرصيدك الحالي: {new_bank_balance:,.2f}$", show_alert=True)
        
        # تحديث واجهة المحفظة
        await process_wallet_logic(owner_id, callback_query.from_user.first_name, callback=callback_query)

    except Exception as e:
        logging.error(f"❌ Error in repay_loan: {e}")
        await callback_query.answer("⚠️ حدث خطأ فني أثناء السداد.", show_alert=True)
        
@dp.callback_query_handler(Text(startswith='loan_menu:'), state="*")
async def loan_menu(callback_query: types.CallbackQuery):
    # 🔐 القفل الأمني
    owner_id = int(callback_query.data.split(':')[1])
    if callback_query.from_user.id != owner_id:
        return await callback_query.answer("⚠️ اطلب قائمة القروض من محفظتك الخاصة!", show_alert=True)
    
    user_data = await get_user_data(owner_id)
    if not user_data: return
    
    current_debt = float(user_data.get('debt_balance', 0) or 0)
    
    if current_debt > 0:
        return await callback_query.answer(f"⚠️ لديك قرض نشط بقيمة {current_debt:,.2f}$، سدده أولاً!", show_alert=True)

    loan_amount = 10000.0  # مبلغ القرض المتاح
    
    markup = InlineKeyboardMarkup()
    # نمرر owner_id في الكولباك للحماية في الخطوة التالية
    markup.add(InlineKeyboardButton(f"💰 اقتراض {loan_amount:,.0f} $ (مرة واحدة)", callback_data=f"exec_loan:{owner_id}:{loan_amount}"))
    markup.add(InlineKeyboardButton("🔙 عودة للمحفظة", callback_data=f"wallet_view:{owner_id}"))
    
    text = (
        f"🏦 | <b>مـركـز الائـتـمـان والـقـروض</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💵 الـمبلغ الـمتاح لك: <b>{loan_amount:,.2f} $</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"<i>* ملاحظة: القروض تساعدك على بدء التداول عند تصفير المحفظة.</i>"
    )

    await callback_query.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    
@dp.callback_query_handler(Text(startswith='exec_loan:'), state="*")
async def exec_loan_handler(callback_query: types.CallbackQuery):
    data = callback_query.data.split(':')
    owner_id = int(data[1])
    loan_amount = float(data[2])
    
    # 🔐 تأكيد الهوية
    if callback_query.from_user.id != owner_id:
        return await callback_query.answer("❌ خطأ في التحقق من الهوية!", show_alert=True)
    
    user_data = await get_user_data(owner_id)
    if not user_data: return

    # حساب القيم الجديدة بدقة float
    new_bank = float(user_data.get('bank_balance', 0) or 0) + loan_amount
    new_debt = float(user_data.get('debt_balance', 0) or 0) + loan_amount

    try:
        # تحديث سوبابيس (بيانات float متوافقة مع numeric)
        supabase.table("users_global_profile").update({
            "bank_balance": new_bank,
            "debt_balance": new_debt
        }).eq("user_id", owner_id).execute()
        
        await callback_query.answer(f"✅ تم منحك قرض بقيمة {loan_amount:,.2f} $ بنجاح!", show_alert=True)
        
        # تحديث واجهة المحفظة فوراً
        await process_wallet_logic(owner_id, callback_query.from_user.first_name, callback=callback_query)
        
    except Exception as e:
        logging.error(f"❌ Loan Error: {e}")
        await callback_query.answer("❌ فشل في تحديث قاعدة البيانات، حاول لاحقاً.", show_alert=True)

import asyncio
import aiohttp
import math
import logging  # تمت الإضافة لحل خطأ الـ logging
from datetime import datetime


# تمت إضافة الدالة المفقودة هنا
async def async_manual_upsert(table_name, records):
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates"
    }
    endpoint = f"{SUPABASE_URL}/rest/v1/{table_name}"
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(endpoint, json=records, headers=headers, timeout=30) as response:
                return response.status in [200, 201]
        except Exception as e:
            logging.error(f"⚠️ خطأ في الرفع: {e}")
            return False

# --- [ 2. دوال الحساب الرياضي الدقيقة (بدون Pandas) ] ---
def calculate_ema(data, period):
    if len(data) < period: return data[-1]
    alpha = 2 / (period + 1)
    ema = sum(data[:period]) / period
    for price in data[period:]:
        ema = (price * alpha) + (ema * (1 - alpha))
    return ema

def calculate_rsi(data, period=14):
    if len(data) < period + 1: return 50.0
    gains, losses = [], []
    for i in range(1, len(data)):
        diff = data[i] - data[i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    if avg_loss == 0: return 100.0
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def calculate_bollinger(data, period=20):
    if len(data) < period: return data[-1], data[-1], data[-1]
    recent = data[-period:]
    sma = sum(recent) / period
    variance = sum((x - sma) ** 2 for x in recent) / period
    std_dev = math.sqrt(variance)
    return sma + (std_dev * 2), sma, sma - (std_dev * 2)

async def fetch_klines(session, symbol, interval, limit=100):
    url = f"https://data-api.binance.vision/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    try:
        # تم تعديل التايم أوت إلى 10 ثواني لضمان عدم فشل الاتصال
        async with session.get(url, timeout=10) as res:
            if res.status == 200: return await res.json()
    except: return None

# --- [ 3. دالة الجلب والتحليل ] ---
async def update_crypto_market_data():
    print(f"\n🚀 {datetime.now().strftime('%H:%M:%S')} | بدء جلب بيانات Binance Vision...")
    
    async with aiohttp.ClientSession() as session:
        try:
            # تم تعديل التايم أوت هنا أيضاً
            async with session.get("https://data-api.binance.vision/api/v3/ticker/24hr", timeout=10) as res:
                if res.status != 200: return
                ticker_data = await res.json()
                if not isinstance(ticker_data, list): return
        except Exception as e:
            logging.error(f"❌ فشل الاتصال بـ API: {e}")
            return

        top_coins = [c for c in ticker_data if isinstance(c, dict) and c.get('symbol', '').endswith('USDT') and float(c.get('lastPrice', 0)) >= 1.0]
        top_coins = sorted(top_coins, key=lambda x: float(x.get('quoteVolume', 0)), reverse=True)[:100]
        
        timeframes = ['15m', '1h', '2h', '4h', '1d']
        final_records = []

        for coin in top_coins:
            symbol = coin.get('symbol')
            if not symbol: continue
            
            try:
                price = float(coin.get('lastPrice', 0))
                change_percent = float(coin.get('priceChangePercent', 0))
                record = {
                    "symbol": symbol,
                    "name": symbol.replace("USDT", ""),
                    "current_price": price,
                    "open_price_24h": float(coin.get('openPrice', 0)),
                    "high_24h": float(coin.get('highPrice', 0)),
                    "low_24h": float(coin.get('lowPrice', 0)),
                    "volume_24h": float(coin.get('volume', 0)),
                    "change_24h": change_percent,
                    "last_tick_direction": "UP" if change_percent >= 0 else "DOWN",
                    "updated_at": "now()"
                }
                
                tasks = [fetch_klines(session, symbol, tf) for tf in timeframes]
                results = await asyncio.gather(*tasks)

                for i, tf in enumerate(timeframes):
                    if results[i] and isinstance(results[i], list):
                        closes = [float(k[4]) for k in results[i]]
                        volumes = [float(k[5]) for k in results[i]]
                        upper, mid, lower = calculate_bollinger(closes)
                        record.update({
                            f"ema_20_{tf}": calculate_ema(closes, 20),
                            f"ema_50_{tf}": calculate_ema(closes, 50),
                            f"ema_100_{tf}": calculate_ema(closes, 100),
                            f"rsi_{tf}": calculate_rsi(closes),
                            f"bb_upper_{tf}": upper, 
                            f"bb_middle_{tf}": mid, 
                            f"bb_lower_{tf}": lower,
                            f"volume_ma_{tf}": sum(volumes[-20:]) / 20
                        })
                final_records.append(record)
            except Exception as e: continue

        if final_records:
            print(f"📦 جاري رفع {len(final_records)} عملة إلى سوبابيس...")
            for i in range(0, len(final_records), 10):
                await async_manual_upsert("crypto_market_simulation", final_records[i:i + 10])
    
    print(f"✅ {datetime.now().strftime('%H:%M:%S')} | تم التحديث بنجاح.")

# --- [ 4. حلقة التشغيل التلقائي ] ---
async def market_updater_background_task():
    """تعمل هذه الدالة في الخلفية لتحديث السوق كل 120 ثانية"""
    while True:
        try:
            await update_crypto_market_data()
            print("⏳ أنتظر 120 ثانية قبل الجولة القادمة...\n")
            await asyncio.sleep(120) 
        except Exception as e:
            logging.error(f"Market Updater Loop Error: {e}")
            await asyncio.sleep(30) # انتظار أقصر عند حدوث خطأ للتعافي السريع


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
    # ب) تشغيل محركات التداول في الخلفية
    logging.info("⏳ جاري تشغيل محركات السوق والرادار...")
    
    # 1. محرك تصفية الصفقات (الذي أرسلته أنت)
    asyncio.create_task(trade_reaper()) 
    
    # 2. محرك تحديث الأسعار والمؤشرات من بينانس (الجديد)
    asyncio.create_task(market_updater_background_task())

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
