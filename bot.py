import os
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler
from telethon import TelegramClient
from telethon.sessions import StringSession

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ['BOT_TOKEN']
API_ID = int(os.environ.get('API_ID', '2040'))
API_HASH = os.environ.get('API_HASH', 'b18441a1ff607e10a989891a5462e627')

PHONE, CODE, PASSWORD = range(3)

class SessionManager:
    def __init__(self):
        self.active_sessions = {}
    
    async def create_client(self, phone: str, user_id: int):
        """Создаем клиента для ручного ввода"""
        try:
            client = TelegramClient(StringSession(), API_ID, API_HASH)
            await client.connect()
            
            self.active_sessions[user_id] = {
                'client': client,
                'phone': phone
            }
            return True, "✅ Клиент создан. Теперь получите код вручную:\n\n1. Откройте официальный Telegram\n2. Введите номер телефона\n3. Получите код\n4. Введите код сюда"
        except Exception as e:
            return False, f"❌ Ошибка: {str(e)}"
    
    async def manual_login(self, user_id: int, code: str):
        """Ручной вход с кодом"""
        if user_id not in self.active_sessions:
            return False, "❌ Сессия не найдена"
        
        data = self.active_sessions[user_id]
        try:
            # Пытаемся войти с кодом (предполагая что код уже запрошен вручную)
            await data['client'].start(phone=data['phone'], code=code)
            
            session_string = data['client'].session.save()
            await data['client'].disconnect()
            del self.active_sessions[user_id]
            
            return True, session_string
        except Exception as e:
            await data['client'].disconnect()
            del self.active_sessions[user_id]
            return False, f"❌ Ошибка входа: {str(e)}"

manager = SessionManager()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "🔐 **Ручной генератор сессий**\n\n"
        "📱 Отправьте номер телефона:\n"
        "Формат: +79123456789\n\n"
        "⚠️ **Инструкция:**\n"
        "1. Введите номер здесь\n"
        "2. Получите код в официальном Telegram\n"
        "3. Введите код сюда"
    )
    return PHONE

async def handle_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    phone = update.message.text.strip()
    user_id = update.effective_user.id
    
    success, message = await manager.create_client(phone, user_id)
    
    if success:
        await update.message.reply_text(
            f"{message}\n\n"
            "📲 **Как получить код:**\n"
            "• Откройте Telegram на телефоне\n"
            "• Введите тот же номер телефона\n"
            "• Дождитесь код\n"
            "• Введите код сюда\n\n"
            "🔢 **Введите код из Telegram:**"
        )
        return CODE
    else:
        await update.message.reply_text(message)
        return ConversationHandler.END

async def handle_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    code = update.message.text.replace(' ', '')
    user_id = update.effective_user.id
    
    success, result = await manager.manual_login(user_id, code)
    
    if success:
        await update.message.reply_document(
            document=result.encode('utf-8'),
            filename='telegram_session.txt',
            caption="✅ **Сессия создана!**\n\nСохраните файл!"
        )
        await update.message.reply_text(f"`{result}`", parse_mode='Markdown')
    else:
        await update.message.reply_text(f"{result}\n\nПопробуйте /start")
    
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("❌ Отменено")
    return ConversationHandler.END

def main():
    application = Application.builder().token(BOT_TOKEN).build()
    
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_phone)],
            CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_code)]
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    
    application.add_handler(conv_handler)
    application.run_polling()

if __name__ == '__main__':
    main()
