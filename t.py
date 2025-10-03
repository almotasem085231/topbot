import os
import logging
import sqlite3
import datetime
import html
import telegram
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters
import re 

# قم بتحميل متغيرات البيئة من ملف .env
# تأكد من إنشاء ملف .env يحتوي على السطر التالي:
# BOT_TOKEN="YOUR_BOT_TOKEN_HERE"
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

# إعداد التسجيل
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

#==============================================================
# قسم قاعدة البيانات
#==============================================================

def init_db():
    """يهيئ قاعدة البيانات وينشئ الجداول."""
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    
    # جدول العداد العام
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS general_counts (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            count INTEGER DEFAULT 0
        )
    """)
    
    # جدول العداد الأسبوعي
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS weekly_counts (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            count INTEGER DEFAULT 0
        )
    """)
    
    # جدول العداد الشهري
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS monthly_counts (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            count INTEGER DEFAULT 0
        )
    """)
    
    # جدول لتخزين تاريخ آخر عملية إعادة ضبط لمنع التكرار
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS last_reset_dates (
            type TEXT PRIMARY KEY,
            last_reset_date TEXT
        )
    """)
    
    conn.commit()
    conn.close()

def update_counts(user_id, username):
    """يزيد عدد الرسائل للمستخدم في جميع الجداول."""
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()

    # تحديث العد العام
    cursor.execute(
        "INSERT OR REPLACE INTO general_counts (user_id, username, count) VALUES (?, ?, COALESCE((SELECT count FROM general_counts WHERE user_id = ?), 0) + 1)",
        (user_id, username, user_id)
    )

    # تحديث العد الأسبوعي
    cursor.execute(
        "INSERT OR REPLACE INTO weekly_counts (user_id, username, count) VALUES (?, ?, COALESCE((SELECT count FROM weekly_counts WHERE user_id = ?), 0) + 1)",
        (user_id, username, user_id)
    )

    # تحديث العد الشهري
    cursor.execute(
        "INSERT OR REPLACE INTO monthly_counts (user_id, username, count) VALUES (?, ?, COALESCE((SELECT count FROM monthly_counts WHERE user_id = ?), 0) + 1)",
        (user_id, username, user_id)
    )

    conn.commit()
    conn.close()

def get_rank_and_count(table_name, user_id):
    """يحصل على ترتيب المستخدم وعدد رسائله."""
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    
    cursor.execute(f"SELECT count FROM {table_name} WHERE user_id = ?", (user_id,))
    user_count = cursor.fetchone()
    if not user_count:
        conn.close()
        return 0, 0
    
    cursor.execute(f"SELECT COUNT(*) FROM {table_name} WHERE count > ?", (user_count[0],))
    rank = cursor.fetchone()[0] + 1
    
    conn.close()
    return user_count[0], rank

def get_top_users(table_name, limit=5):
    """يحصل على قائمة بأعلى المستخدمين."""
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    
    cursor.execute(f"SELECT username, count FROM {table_name} ORDER BY count DESC LIMIT ?", (limit,))
    top_users = cursor.fetchall()
    
    conn.close()
    return top_users

def reset_counts():
    """
    يتحقق من التاريخ لإعادة ضبط العدادات الأسبوعية (الأحد) والشهرية (اليوم الأول).
    """
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    
    today = datetime.date.today()
    today_str = today.strftime('%Y-%m-%d')
    
    # --- إعادة ضبط العدادات الأسبوعية ---
    # الأحد هو اليوم 6 في نظام datetime.weekday()
    if today.weekday() == 6:
        cursor.execute("SELECT last_reset_date FROM last_reset_dates WHERE type = 'weekly'")
        last_reset = cursor.fetchone()
        
        # لا يتم إعادة الضبط إلا إذا كان تاريخ آخر إعادة ضبط ليس اليوم
        if not last_reset or last_reset[0] != today_str:
            cursor.execute("DELETE FROM weekly_counts")
            cursor.execute("INSERT OR REPLACE INTO last_reset_dates (type, last_reset_date) VALUES ('weekly', ?)", (today_str,))
            logger.info("تم إعادة ضبط العدادات الأسبوعية (الأحد) بنجاح.")

    # --- إعادة ضبط العدادات الشهرية ---
    # اليوم الأول من الشهر
    if today.day == 1:
        cursor.execute("SELECT last_reset_date FROM last_reset_dates WHERE type = 'monthly'")
        last_reset = cursor.fetchone()
        
        # لا يتم إعادة الضبط إلا إذا كان تاريخ آخر إعادة ضبط ليس اليوم
        if not last_reset or last_reset[0] != today_str:
            cursor.execute("DELETE FROM monthly_counts")
            cursor.execute("INSERT OR REPLACE INTO last_reset_dates (type, last_reset_date) VALUES ('monthly', ?)", (today_str,))
            logger.info("تم إعادة ضبط العدادات الشهرية بنجاح.")

    conn.commit()
    conn.close()

#==============================================================
# قسم معالجات الأوامر والرسائل
#==============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """معالج أمر /start."""
    await update.message.reply_text('أهلاً بك، أنا بوت لحساب عدد الرسائل وتصنيفها.')

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """معالج الرسائل النصية لحسابها."""
    if not update.message or not update.message.from_user:
        return

    # يتم استدعاء reset_counts مع كل رسالة
    reset_counts()
    
    user_id = update.message.from_user.id
    username = update.message.from_user.username or update.message.from_user.first_name
    
    if user_id == context.bot.id:
        return
        
    update_counts(user_id, html.escape(username))
    

async def my_total_rank(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """معالج لـ 'رسايلي' أو '/total_messages' لعرض العدد والترتيب الكلي."""
    user_id = update.message.from_user.id
    count, rank = get_rank_and_count('general_counts', user_id) 
    if count == 0:
        await update.message.reply_text("لم ترسل أي رسائل في الإحصائيات الكلية بعد.")
    else:
        await update.message.reply_text(f"عدد رسائلك الكلي: <b>{count}</b>\nترتيبك الكلي: <b>{rank}</b>", parse_mode='HTML')


async def my_weekly_rank(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """معالج أمر /my_weekly_rank."""
    user_id = update.message.from_user.id
    count, rank = get_rank_and_count('weekly_counts', user_id)
    if count == 0:
        await update.message.reply_text("لم ترسل أي رسائل هذا الأسبوع بعد.")
    else:
        await update.message.reply_text(f"عدد رسائلك لهذا الأسبوع: <b>{count}</b>\nترتيبك: <b>{rank}</b>", parse_mode='HTML')

async def my_monthly_rank(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """معالج أمر /my_monthly_rank."""
    user_id = update.message.from_user.id
    count, rank = get_rank_and_count('monthly_counts', user_id)
    if count == 0:
        await update.message.reply_text("لم ترسل أي رسائل هذا الشهر بعد.")
    else:
        await update.message.reply_text(f"عدد رسائلك لهذا الشهر: <b>{count}</b>\nترتيبك: <b>{rank}</b>", parse_mode='HTML')
        
async def top_ranks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """معالج عام لأوامر التوب (للمشرفين فقط)."""
    command = update.message.text.split()[0]
    
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    
    if update.effective_chat.type not in [telegram.constants.ChatType.GROUP, telegram.constants.ChatType.SUPERGROUP]:
        await update.message.reply_text("هذا الأمر يعمل فقط داخل المجموعات.")
        return

    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        if member.status not in [telegram.constants.ChatMemberStatus.ADMINISTRATOR, telegram.constants.ChatMemberStatus.OWNER]:
            await update.message.reply_text("عذراً، هذا الأمر متاح للمشرفين فقط.")
            return
    except Exception as e:
        logger.error(f"خطأ في التحقق من صلاحيات المشرف: {e}")
        await update.message.reply_text("حدث خطأ أثناء محاولة التحقق من صلاحياتك.")
        return
        
    table_name = 'weekly_counts' if 'weekly' in command else 'monthly_counts'
    limit = 20 if '20' in command else 5
    
    top_users = get_top_users(table_name, limit)
    
    if not top_users:
        await update.message.reply_text("لا يوجد مستخدمون مصنفون بعد.")
        return
    
    plural_form = "مستخدمين" if limit == 5 else "مستخدم"

    if 'weekly' in command:
        title = f"أعلى {limit} {plural_form} لهذا الأسبوع:"
    else:
        title = f"أعلى {limit} {plural_form} لهذا الشهر:"

    message_text = f"<b>{html.escape(title)}</b>\n\n"
    
    for i, (username, count) in enumerate(top_users):
        emoji = ""
        if i == 0: emoji = "🥇"
        elif i == 1: emoji = "🥈"
        elif i == 2: emoji = "🥉"
        message_text += f"{i + 1}. {username}: {count} رسالة {emoji}\n"
    
    await update.message.reply_text(message_text, parse_mode='HTML')

#==============================================================
# قسم التطبيق الرئيسي
#==============================================================

def main():
    """الدالة الرئيسية لتشغيل البوت."""
    if not BOT_TOKEN:
        logger.error("لم يتم العثور على رمز البوت. يرجى التحقق من ملف .env")
        return
        
    init_db()

    application = ApplicationBuilder().token(BOT_TOKEN).read_timeout(10).write_timeout(10).build()

    # معالجات الأوامر الفردية (تبدأ بـ /)
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("my_weekly_rank", my_weekly_rank))
    application.add_handler(CommandHandler("my_monthly_rank", my_monthly_rank))
    
    # المعالج رقم 1: لالتقاط كلمة "رسايلي" كنص عادي (بدون /) - الحل الذي لا يسبب أخطاء توافق
    application.add_handler(MessageHandler(
        filters.TEXT & filters.Regex(r'^رسايلي$'), 
        my_total_rank
    ))
    
    # المعالج رقم 2: الأمر الرسمي باللاتينية (لأن أوامر تليجرام لا تقبل الحروف العربية)
    application.add_handler(CommandHandler("total_messages", my_total_rank)) 

    # معالجات أوامر التوب العامة
    application.add_handler(CommandHandler("top5_weekly", top_ranks))
    application.add_handler(CommandHandler("top5_monthly", top_ranks))
    application.add_handler(CommandHandler("top20_weekly", top_ranks))
    application.add_handler(CommandHandler("top20_monthly", top_ranks))

    # معالج لجميع الرسائل النصية التي ليست أوامر (يجب أن يكون في النهاية)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # بدء تشغيل البوت عبر الاستقصاء (Polling)
    application.run_polling()

if __name__ == '__main__':
    main()
