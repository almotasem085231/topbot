import os
import logging
import sqlite3
import datetime
import html
import telegram
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters

# قم بتحميل متغيرات البيئة من ملف .env
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
    """يقوم بتهيئة قاعدة البيانات وإنشاء الجداول إذا لم تكن موجودة."""
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS general_counts (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            count INTEGER DEFAULT 0
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS weekly_counts (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            count INTEGER DEFAULT 0
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS monthly_counts (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            count INTEGER DEFAULT 0
        )
    """)
    
    conn.commit()
    conn.close()

def update_counts(user_id, username):
    """يزيد عدد الرسائل للمستخدم في جميع الجداول."""
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()

    cursor.execute(
        "INSERT OR REPLACE INTO general_counts (user_id, username, count) VALUES (?, ?, COALESCE((SELECT count FROM general_counts WHERE user_id = ?), 0) + 1)",
        (user_id, username, user_id)
    )

    cursor.execute(
        "INSERT OR REPLACE INTO weekly_counts (user_id, username, count) VALUES (?, ?, COALESCE((SELECT count FROM weekly_counts WHERE user_id = ?), 0) + 1)",
        (user_id, username, user_id)
    )

    cursor.execute(
        "INSERT OR REPLACE INTO monthly_counts (user_id, username, count) VALUES (?, ?, COALESCE((SELECT count FROM monthly_counts WHERE user_id = ?), 0) + 1)",
        (user_id, username, user_id)
    )

    conn.commit()
    conn.close()

def get_rank_and_count(table_name, user_id):
    """يحصل على ترتيب المستخدم وعدد رسائله من جدول معين."""
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
    """يحصل على قائمة بأعلى المستخدمين من جدول معين."""
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    
    cursor.execute(f"SELECT username, count FROM {table_name} ORDER BY count DESC LIMIT ?", (limit,))
    top_users = cursor.fetchall()
    
    conn.close()
    return top_users

def reset_counts():
    """يتحقق من التاريخ لإعادة ضبط العدادات الأسبوعية والشهرية."""
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    
    today = datetime.date.today()
    
    if today.weekday() == 0:  # الاثنين
        cursor.execute("DELETE FROM weekly_counts")
        logger.info("تم إعادة ضبط العدادات الأسبوعية بنجاح.")
        
    if today.day == 1:
        cursor.execute("DELETE FROM monthly_counts")
        logger.info("تم إعادة ضبط العدادات الشهرية بنجاح.")

    conn.commit()
    conn.close()

#==============================================================
# قسم معالجات الأوامر والرسائل
#==============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """معالج أمر /start."""
    await update.message.reply_text('أهلاً بك، أنا بوت لحساب عدد الرسائل.')

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """معالج الرسائل النصية لحسابها."""
    if not update.message or not update.message.from_user:
        return

    reset_counts()
    
    user_id = update.message.from_user.id
    username = update.message.from_user.username or update.message.from_user.first_name
    
    if user_id == context.bot.id:
        return
        
    update_counts(user_id, username)

async def my_weekly_rank(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """معالج أمر /my_weekly_rank."""
    user_id = update.message.from_user.id
    count, rank = get_rank_and_count('weekly_counts', user_id)
    if count == 0:
        await update.message.reply_text("لم ترسل أي رسائل هذا الأسبوع بعد.")
    else:
        await update.message.reply_text(f"عدد رسائلك لهذا الأسبوع: {count}\nترتيبك: {rank}")

async def my_monthly_rank(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """معالج أمر /my_monthly_rank."""
    user_id = update.message.from_user.id
    count, rank = get_rank_and_count('monthly_counts', user_id)
    if count == 0:
        await update.message.reply_text("لم ترسل أي رسائل هذا الشهر بعد.")
    else:
        await update.message.reply_text(f"عدد رسائلك لهذا الشهر: {count}\nترتيبك: {rank}")
        
async def top_ranks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """معالج عام لأوامر التوب (للمشرفين فقط)."""
    command = update.message.text.split()[0]
    
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        if member.status not in [telegram.constants.ChatMemberStatus.ADMINISTRATOR, telegram.constants.ChatMemberStatus.OWNER]:
            await update.message.reply_text("عذراً، هذا الأمر متاح للمشرفين فقط.")
            return
    except telegram.error.TimedOut:
        await update.message.reply_text("انتهت مهلة طلب التحقق من صلاحيات المشرف. يرجى المحاولة مرة أخرى لاحقًا.")
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
    
    if 'weekly' in command:
        # هنا تم تصحيح العبارة
        title = "أعلى {} مستخدم لهذا الأسبوع:".format(limit)
    else:
        # هنا تم تصحيح العبارة
        title = "أعلى {} مستخدم لهذا الشهر:".format(limit)

    message_text = f"<b>{html.escape(title)}</b>\n\n"
    
    for i, (username, count) in enumerate(top_users):
        emoji = ""
        if i == 0: emoji = "🥇"
        elif i == 1: emoji = "🥈"
        elif i == 2: emoji = "🥉"
        message_text += f"{i + 1}. {html.escape(username)}: {count} رسالة {emoji}\n"
    
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

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("my_weekly_rank", my_weekly_rank))
    application.add_handler(CommandHandler("my_monthly_rank", my_monthly_rank))
    
    application.add_handler(CommandHandler("top5_weekly", top_ranks))
    application.add_handler(CommandHandler("top5_monthly", top_ranks))
    application.add_handler(CommandHandler("top20_weekly", top_ranks))
    application.add_handler(CommandHandler("top20_monthly", top_ranks))

    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    application.run_polling()

if __name__ == '__main__':
    main()
