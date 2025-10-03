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
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
# ****** الجديد: قراءة هوية المجموعة المسموح بها ******
ALLOWED_GROUP_ID = os.getenv("ALLOWED_GROUP_ID") 
# تحويل الـ ID إلى عدد صحيح للتأكد من استخدامه بشكل صحيح في الفلاتر
try:
    ALLOWED_GROUP_ID = int(ALLOWED_GROUP_ID)
except (ValueError, TypeError):
    logger.error("ALLOWED_GROUP_ID غير صالح. يرجى التحقق من ملف .env.")
    ALLOWED_GROUP_ID = None


# إعداد التسجيل
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

#==============================================================
# قسم قاعدة البيانات (بدون تغيير)
#==============================================================

def init_db():
    """يهيئ قاعدة البيانات وينشئ الجداول."""
    # (نفس دالة init_db السابقة)
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
    # (نفس دالة update_counts السابقة)
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
    """يحصل على ترتيب المستخدم وعدد رسائله."""
    # (نفس دالة get_rank_and_count السابقة)
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
    # (نفس دالة get_top_users السابقة)
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
    # (نفس دالة reset_counts السابقة - تم ضبطها على الأحد 6)
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    
    today = datetime.date.today()
    today_str = today.strftime('%Y-%m-%d')
    
    # إعادة ضبط العدادات الأسبوعية (الأحد)
    if today.weekday() == 6:
        cursor.execute("SELECT last_reset_date FROM last_reset_dates WHERE type = 'weekly'")
        last_reset = cursor.fetchone()
        if not last_reset or last_reset[0] != today_str:
            cursor.execute("DELETE FROM weekly_counts")
            cursor.execute("INSERT OR REPLACE INTO last_reset_dates (type, last_reset_date) VALUES ('weekly', ?)", (today_str,))
            logger.info("تم إعادة ضبط العدادات الأسبوعية (الأحد) بنجاح.")

    # إعادة ضبط العدادات الشهرية (اليوم الأول من الشهر)
    if today.day == 1:
        cursor.execute("SELECT last_reset_date FROM last_reset_dates WHERE type = 'monthly'")
        last_reset = cursor.fetchone()
        if not last_reset or last_reset[0] != today_str:
            cursor.execute("DELETE FROM monthly_counts")
            cursor.execute("INSERT OR REPLACE INTO last_reset_dates (type, last_reset_date) VALUES ('monthly', ?)", (today_str,))
            logger.info("تم إعادة ضبط العدادات الشهرية بنجاح.")

    conn.commit()
    conn.close()

#==============================================================
# قسم معالجات الأوامر والرسائل
#==============================================================

# تم إزالة معالج /start للتركيز على المجموعة فقط.

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """معالج الرسائل النصية لحسابها (يعمل فقط في المجموعة المسموح بها)."""
    if not update.message or not update.message.from_user:
        return
    
    # الشرط الإضافي: تجاهل الرسالة إذا لم يكن الـ Chat ID هو المسموح به
    # هذا الفحص قد يكون مكرراً إذا تم تطبيق فلتر Chat() في الأسفل، لكنه يضيف طبقة أمان داخل الدالة.
    if update.message.chat_id != ALLOWED_GROUP_ID:
        return

    # يتم استدعاء reset_counts مع كل رسالة
    reset_counts()
    
    user_id = update.message.from_user.id
    username = update.message.from_user.username or update.message.from_user.first_name
    
    if user_id == context.bot.id:
        return
        
    update_counts(user_id, html.escape(username))
    

async def my_total_rank(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """معالج لعرض العدد والترتيب الكلي."""
    # يجب أن يتم التأكد من أن هذا الأمر يعمل فقط في المجموعة المسموح بها
    # الفلتر في دالة main يضمن ذلك
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
    """معالج عام لأوامر التوب (يعمل فقط في المجموعة وللمشرفين)."""
    # الفلتر في دالة main يضمن عمله في المجموعة المحددة
    
    command = update.message.text.split()[0]
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    
    # بما أننا قمنا بتقييد الأمر للمجموعة المسموح بها فقط، لم نعد بحاجة لفحص نوع الشات.
    
    # التحقق من صلاحيات المشرف
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
    if not BOT_TOKEN or not ALLOWED_GROUP_ID:
        logger.error("لم يتم العثور على رمز البوت أو هوية المجموعة المسموح بها. يرجى التحقق من ملف .env")
        return
        
    init_db()

    application = ApplicationBuilder().token(BOT_TOKEN).read_timeout(10).write_timeout(10).build()

    # ****** تطبيق الفلتر على جميع المعالجات ******
    # نستخدم filters.Chat(chat_id=ALLOWED_GROUP_ID) لضمان أن جميع الإحصائيات 
    # وأوامر العد تعمل فقط داخل تلك المجموعة.

    allowed_chat_filter = filters.Chat(chat_id=ALLOWED_GROUP_ID)
    
    # معالجات الأوامر الفردية (تبدأ بـ /) - مقتصرة على المجموعة
    application.add_handler(CommandHandler("my_weekly_rank", my_weekly_rank, filters=allowed_chat_filter))
    application.add_handler(CommandHandler("my_monthly_rank", my_monthly_rank, filters=allowed_chat_filter))
    
    # معالج "رسايلي" كنص عادي - مقتصر على المجموعة
    application.add_handler(MessageHandler(
        allowed_chat_filter & filters.TEXT & filters.Regex(r'^رسايلي$'), 
        my_total_rank
    ))
    
    # معالج الأمر الرسمي /total_messages - مقتصر على المجموعة
    application.add_handler(CommandHandler("total_messages", my_total_rank, filters=allowed_chat_filter)) 

    # معالجات أوامر التوب العامة - مقتصرة على المجموعة
    application.add_handler(CommandHandler("top5_weekly", top_ranks, filters=allowed_chat_filter))
    application.add_handler(CommandHandler("top5_monthly", top_ranks, filters=allowed_chat_filter))
    application.add_handler(CommandHandler("top20_weekly", top_ranks, filters=allowed_chat_filter))
    application.add_handler(CommandHandler("top20_monthly", top_ranks, filters=allowed_chat_filter))

    # معالج لجميع الرسائل النصية التي ليست أوامر - مقتصر على المجموعة
    # هذا هو المعالج الذي يقوم بالعد.
    application.add_handler(MessageHandler(allowed_chat_filter & filters.TEXT & ~filters.COMMAND, handle_message))

    # ملاحظة حول المحادثات الخاصة:
    # بما أن جميع معالجات الإحصائيات والعد مُقيدة بـ allowed_chat_filter،
    # فإن أي رسالة أو أمر إحصائي يُرسل في محادثة خاصة سيتم تجاهله ببساطة، 
    # مما يحقق متطلب عدم ظهور الإحصائيات في الخاص.
    
    # يمكنك إضافة معالج بسيط لأوامر البداية في الخاص لإعلام المستخدمين:
    async def private_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text("عذراً، هذا البوت مخصص لمجموعة محددة، ولا يقوم بحساب أو عرض الإحصائيات في المحادثات الخاصة.")
        
    application.add_handler(CommandHandler("start", private_start, filters=filters.ChatType.PRIVATE))

    # بدء تشغيل البوت عبر الاستقصاء (Polling)
    application.run_polling()

if __name__ == '__main__':
    main()
