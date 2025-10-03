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

#==============================================================
# قسم إعداد التسجيل
#==============================================================

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

#==============================================================
# قسم تهيئة المتغيرات
#==============================================================

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = os.getenv("OWNER_ID") 

try:
    OWNER_ID = int(OWNER_ID)
except (ValueError, TypeError):
    logger.error("OWNER_ID غير صالح أو مفقود. يرجى التأكد من إضافة OWNER_ID=\"YOUR_TELEGRAM_USER_ID\" في ملف .env.")
    OWNER_ID = None

#==============================================================
# الفلتر المخصص للمجموعة (بدون تغيير)
#==============================================================

class IsAllowedGroup(filters.BaseFilter):
    """فلتر مخصص للتحقق مما إذا كانت المجموعة مسموحاً بها عبر قاعدة البيانات."""
    
    def filter(self, update: Update):
        if update.effective_chat is None:
            return False
        
        chat_id = update.effective_chat.id
        return is_group_allowed(chat_id)

ALLOWED_GROUP_FILTER = IsAllowedGroup()

#==============================================================
# دوال قاعدة البيانات (بدون تغيير)
#==============================================================

def init_db():
    """يهيئ قاعدة البيانات وينشئ الجداول."""
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    
    # جداول العدادات
    cursor.execute("CREATE TABLE IF NOT EXISTS general_counts (user_id INTEGER PRIMARY KEY, username TEXT, count INTEGER DEFAULT 0)")
    cursor.execute("CREATE TABLE IF NOT EXISTS weekly_counts (user_id INTEGER PRIMARY KEY, username TEXT, count INTEGER DEFAULT 0)")
    cursor.execute("CREATE TABLE IF NOT EXISTS monthly_counts (user_id INTEGER PRIMARY KEY, username TEXT, count INTEGER DEFAULT 0)")
    cursor.execute("CREATE TABLE IF NOT EXISTS last_reset_dates (type TEXT PRIMARY KEY, last_reset_date TEXT)")
    cursor.execute("CREATE TABLE IF NOT EXISTS allowed_groups (chat_id INTEGER PRIMARY KEY)")
    
    # جدول المشرفين
    cursor.execute("CREATE TABLE IF NOT EXISTS supervisors (user_id INTEGER PRIMARY KEY)")
    
    conn.commit()
    conn.close()

def is_supervisor(user_id):
    """يتحقق مما إذا كان المستخدم مشرفًا أو المالك."""
    if user_id == OWNER_ID:
        return True
        
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM supervisors WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result is not None

def add_supervisor_to_db(user_id):
    """يضيف معرف مستخدم إلى قائمة المشرفين في DB."""
    if user_id == OWNER_ID:
        return False
        
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT OR IGNORE INTO supervisors (user_id) VALUES (?)", (user_id,))
        conn.commit()
        return cursor.rowcount > 0 
    finally:
        conn.close()

def is_group_allowed(chat_id):
    """يتحقق مما إذا كانت المجموعة مسموحاً بها في قاعدة البيانات."""
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM allowed_groups WHERE chat_id = ?", (chat_id,))
    result = cursor.fetchone()
    conn.close()
    return result is not None

def add_allowed_group(chat_id):
    """يضيف معرف مجموعة إلى قائمة المجموعات المسموح بها."""
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT OR IGNORE INTO allowed_groups (chat_id) VALUES (?)", (chat_id,))
        conn.commit()
        return cursor.rowcount > 0 
    finally:
        conn.close()

def reset_counts():
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    today = datetime.date.today()
    today_str = today.strftime('%Y-%m-%d')
    if today.weekday() == 6:
        cursor.execute("SELECT last_reset_date FROM last_reset_dates WHERE type = 'weekly'")
        last_reset = cursor.fetchone()
        if not last_reset or last_reset[0] != today_str:
            cursor.execute("DELETE FROM weekly_counts")
            cursor.execute("INSERT OR REPLACE INTO last_reset_dates (type, last_reset_date) VALUES ('weekly', ?)", (today_str,))
            logger.info("تم إعادة ضبط العدادات الأسبوعية (الأحد) بنجاح.")
    if today.day == 1:
        cursor.execute("SELECT last_reset_date FROM last_reset_dates WHERE type = 'monthly'")
        last_reset = cursor.fetchone()
        if not last_reset or last_reset[0] != today_str:
            cursor.execute("DELETE FROM monthly_counts")
            cursor.execute("INSERT OR REPLACE INTO last_reset_dates (type, last_reset_date) VALUES ('monthly', ?)", (today_str,))
            logger.info("تم إعادة ضبط العدادات الشهرية بنجاح.")
    conn.commit()
    conn.close()

def update_counts(user_id, username):
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
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute(f"SELECT username, count FROM {table_name} ORDER BY count DESC LIMIT ?", (limit,))
    top_users = cursor.fetchall()
    conn.close()
    return top_users

#==============================================================
# قسم معالج الأمر /add_supervisor (مُعدَّل)
#==============================================================

async def add_supervisor(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """يضيف مستخدمًا كـ مشرف عبر الرد أو إدخال الـ ID مباشرة."""
    
    # 1. التحقق من صلاحيات المالك
    if update.message.from_user.id != OWNER_ID:
        await update.message.reply_text("عذراً، هذا الأمر متاح فقط لمالك البوت.")
        return
        
    target_id = None
    target_name = None

    # 2. فحص الوسائط (Arguments)
    if context.args:
        try:
            target_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("صيغة غير صحيحة. يرجى إدخال معرف رقمي للمستخدم (User ID).")
            return
        
        # محاولة جلب اسم المستخدم عبر الـ ID
        try:
            target_user = await context.bot.get_chat(target_id)
            target_name = html.escape(target_user.username or target_user.first_name)
        except Exception:
            target_name = f"ID: <code>{target_id}</code>"
    
    # 3. فحص الرد (Reply)
    elif update.message.reply_to_message:
        target_user = update.message.reply_to_message.from_user
        target_id = target_user.id
        target_name = html.escape(target_user.username or target_user.first_name)
    
    # 4. عدم وجود لا وسيط ولا رد
    else:
        await update.message.reply_text("يرجى استخدام الأمر بإحدى الطريقتين:\n1. الرد على رسالة المستخدم في أي مجموعة.\n2. إدخال معرف المستخدم (User ID) بعد الأمر مباشرة، مثال: <code>/add_supervisor 123456789</code>", parse_mode='HTML')
        return

    # 5. إجراء الإضافة
    
    if target_id == OWNER_ID:
        await update.message.reply_text("لا يمكن تعيين المالك كمشرف.")
        return

    if add_supervisor_to_db(target_id):
        await update.message.reply_text(f"تم تعيين المستخدم <a href='tg://user?id={target_id}'>{target_name}</a> كمشرف بنجاح.\nالآن يمكنه استخدام أمر `/add_group`.", parse_mode='HTML')
        logger.info(f"تم تعيين مشرف جديد: {target_id}")
    else:
        await update.message.reply_text(f"المستخدم <a href='tg://user?id={target_id}'>{target_name}</a> هو بالفعل مشرف.", parse_mode='HTML')


async def add_group(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """يضيف المجموعة الحالية إلى قائمة المجموعات المسموح بها (للمالك والمشرفين)."""
    
    user_id = update.message.from_user.id
    
    if not is_supervisor(user_id):
        await update.message.reply_text("عذراً، هذا الأمر متاح فقط لمالك البوت أو المشرفين المعتمدين.")
        return
        
    chat_id = update.message.chat_id
    
    if update.effective_chat.type not in [telegram.constants.ChatType.GROUP, telegram.constants.ChatType.SUPERGROUP]:
        await update.message.reply_text("يجب استخدام هذا الأمر داخل المجموعة التي تريد تفعيل البوت فيها.")
        return
        
    if add_allowed_group(chat_id):
        await update.message.reply_text(f"تم تفعيل البوت بنجاح في هذه المجموعة!\n(Chat ID: <code>{chat_id}</code>)", parse_mode='HTML')
        logger.info(f"تم إضافة مجموعة جديدة بنجاح بواسطة {user_id}: {chat_id}")
    else:
        await update.message.reply_text(f"البوت مُفعل بالفعل في هذه المجموعة (Chat ID: <code>{chat_id}</code>).", parse_mode='HTML')


#==============================================================
# قسم معالجات الأوامر الأخرى (بدون تغيير)
#==============================================================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.from_user:
        return
    if not is_group_allowed(update.message.chat_id):
        return
    reset_counts()
    user_id = update.message.from_user.id
    username = update.message.from_user.username or update.message.from_user.first_name
    if user_id == context.bot.id:
        return
    update_counts(user_id, html.escape(username))
    

async def private_chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("عذراً، هذا البوت مخصص لمجموعة محددة، ولا يقوم بحساب أو عرض الإحصائيات في المحادثات الخاصة.")

async def my_total_rank(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.message.from_user.id
    count, rank = get_rank_and_count('general_counts', user_id) 
    if count == 0:
        await update.message.reply_text("لم ترسل أي رسائل في الإحصائيات الكلية بعد.")
    else:
        await update.message.reply_text(f"عدد رسائلك الكلي: <b>{count}</b>\nترتيبك الكلي: <b>{rank}</b>", parse_mode='HTML')

async def my_weekly_rank(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.message.from_user.id
    count, rank = get_rank_and_count('weekly_counts', user_id)
    if count == 0:
        await update.message.reply_text("لم ترسل أي رسائل هذا الأسبوع بعد.")
    else:
        await update.message.reply_text(f"عدد رسائلك لهذا الأسبوع: <b>{count}</b>\nترتيبك: <b>{rank}</b>", parse_mode='HTML')

async def my_monthly_rank(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.message.from_user.id
    count, rank = get_rank_and_count('monthly_counts', user_id)
    if count == 0:
        await update.message.reply_text("لم ترسل أي رسائل هذا الشهر بعد.")
    else:
        await update.message.reply_text(f"عدد رسائلك لهذا الشهر: <b>{count}</b>\nترتيبك: <b>{rank}</b>", parse_mode='HTML')
        
async def top_ranks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    command = update.message.text.split()[0]
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    
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
# قسم التطبيق الرئيسي (بدون تغيير في الهيكل)
#==============================================================

def main():
    """الدالة الرئيسية لتشغيل البوت."""
    if not BOT_TOKEN or OWNER_ID is None:
        logger.error("خطأ حرج: البوت لا يمكنه العمل بدون رمز البوت أو معرف المالك الصحيح.")
        return
        
    init_db()

    application = ApplicationBuilder().token(BOT_TOKEN).read_timeout(10).write_timeout(10).build()
    
    # 1. معالج الأوامر الإدارية (للمالك فقط)
    owner_filter = filters.User(user_id=OWNER_ID)
    
    # هذا الأمر يعمل الآن في أي مكان، لكنه مقيد بالمالك
    application.add_handler(CommandHandler("add_supervisor", add_supervisor, filters=owner_filter))
    
    # الأمر /add_group متاح للمالك والمشرفين
    application.add_handler(CommandHandler("add_group", add_group))
    
    # 2. معالج المحادثات الخاصة 
    application.add_handler(MessageHandler(filters.ChatType.PRIVATE, private_chat_handler))

    # 3. معالجات المجموعات المسموح بها
    allowed_group_filter = ALLOWED_GROUP_FILTER 

    application.add_handler(CommandHandler("my_weekly_rank", my_weekly_rank, filters=allowed_group_filter))
    application.add_handler(CommandHandler("my_monthly_rank", my_monthly_rank, filters=allowed_group_filter))
    
    application.add_handler(MessageHandler(
        allowed_group_filter & filters.TEXT & filters.Regex(r'^رسايلي$'), 
        my_total_rank
    ))
    
    application.add_handler(CommandHandler("total_messages", my_total_rank, filters=allowed_group_filter)) 

    application.add_handler(CommandHandler("top5_weekly", top_ranks, filters=allowed_group_filter))
    application.add_handler(CommandHandler("top5_monthly", top_ranks, filters=allowed_group_filter))
    application.add_handler(CommandHandler("top20_weekly", top_ranks, filters=allowed_group_filter))
    application.add_handler(CommandHandler("top20_monthly", top_ranks, filters=allowed_group_filter))

    application.add_handler(MessageHandler(allowed_group_filter & filters.TEXT & ~filters.COMMAND, handle_message))

    application.run_polling()

if __name__ == '__main__':
    main()
