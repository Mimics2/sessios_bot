import os
import logging
import asyncio
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError

# Конфиг для Railway
BOT_TOKEN = os.environ['BOT_TOKEN']
API_ID = int(os.environ.get('API_ID', '2040'))
API_HASH = os.environ.get('API_HASH', 'b18441a1ff607e10a989891a5462e627')

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

PHONE, CODE, PASSWORD = range(3)

class SessionManager:
    def __init__(self):
        self.active_sessions = {}
    
    async def start_session(self, phone: str, user_id: int):
        """Начинаем новую сессию"""
        try:
            client = TelegramClient(StringSession(), API_ID, API_HASH)
            await client.connect()
            
            sent_code = await client.send_code_request(phone)
            self.active_sessions[user_id] = {
                'client': client,
                'phone': phone,
                'phone_code_hash': sent_code.phone_code_hash
            }
            return True, "✅ Код отправлен!"
        except Exception as e:
            logger.error(f"Session error: {e}")
            return False, f"❌ Ошибка: {str(e)}"
    
    async def verify_code(self, user_id: int, code: str):
        """Проверяем код"""
        if user_id not in self.active_sessions:
            return False, "Сессия не найдена"
        
        data = self.active_sessions[user_id]
        try:
            await data['client'].sign_in(
                data['phone'], 
                code, 
                phone_code_hash=data['phone_code_hash']
            )
            
            session_string = data['client'].session.save()
            await data['client'].disconnect()
            del self.active_sessions[user_id]
            
            return True, session_string
            
        except SessionPasswordNeededError:
            return False, "2FA_NEEDED"
        except PhoneCodeInvalidError:
            return False, "Неверный код"
        except Exception as e:
            await data['client'].disconnect()
            del self.active_sessions[user_id]
            return False, f"Ошибка: {str(e)}"
    
    async def verify_2fa(self, user_id: int, password: str):
        """Проверяем 2FA пароль"""
        if user_id not in self.active_sessions:
            return False, "Сессия не найдена"
        
        data = self.active_sessions[user_id]
        try:
            await data['client'].sign_in(password=password)
            session_string = data['client'].session.save()
            await data['client'].disconnect()
            del self.active_sessions[user_id]
            return True, session_string
        except Exception as e:
            await data['client'].disconnect()
            del self.active_sessions[user_id]
            return False, f"Ошибка: {str(e)}"

manager = SessionManager()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "🔐 **Session Generator**\n\n"
        "Отправьте номер телефона в международном формате:\n"
        "Пример: +79123456789"
    )
    return PHONE

async def handle_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    phone = update.message.text.strip()
    user_id = update.effective_user.id
    
    if not phone.startswith('+'):
        await update.message.reply_text("❌ Используйте формат +79123456789")
        return PHONE
    
    success, message = await manager.start_session(phone, user_id)
    if success:
        await update.message.reply_text("📲 Код отправлен! Введите код:")
        return CODE
    else:
        await update.message.reply_text(f"{message}\n\nИспользуйте /start для повторной попытки.")
        return ConversationHandler.END

async def handle_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    code = update.message.text.replace(' ', '')
    user_id = update.effective_user.id
    
    if not code.isdigit():
        await update.message.reply_text("❌ Код должен содержать только цифры")
        return CODE
    
    success, result = await manager.verify_code(user_id, code)
    
    if success:
        # Отправляем файл с сессией
        phone = manager.active_sessions.get(user_id, {}).get('phone', 'unknown')
        filename = f"session_{phone.replace('+', '')}.txt"
        
        await update.message.reply_document(
            document=result.encode('utf-8'),
            filename=filename,
            caption=f"✅ Сессия создана для {phone}\n\n⚠️ Сохраните файл в безопасном месте!"
        )
        
        # Также отправляем raw string
        await update.message.reply_text(
            f"📋 **Session String:**\n```\n{result}\n```",
            parse_mode='Markdown'
        )
        
    elif result == "2FA_NEEDED":
        await update.message.reply_text("🔐 Введите пароль 2FA:")
        return PASSWORD
    else:
        await update.message.reply_text(f"❌ {result}\n\nИспользуйте /start для повторной попытки.")
    
    return ConversationHandler.END

async def handle_password(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    password = update.message.text
    user_id = update.effective_user.id
    
    success, session_string = await manager.verify_2fa(user_id, password)
    
    if success:
        phone = manager.active_sessions.get(user_id, {}).get('phone', 'unknown')
        filename = f"session_{phone.replace('+', '')}.txt"
        
        await update.message.reply_document(
            document=session_string.encode('utf-8'),
            filename=filename,
            caption=f"✅ Сессия создана для {phone}"
        )
        
        await update.message.reply_text(
            f"📋 **Session String:**\n```\n{session_string}\n```",
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(f"❌ {session_string}\n\nИспользуйте /start для повторной попытки.")
    
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    if user_id in manager.active_sessions:
        try:
            await manager.active_sessions[user_id]['client'].disconnect()
            del manager.active_sessions[user_id]
        except:
            pass
    
    await update.message.reply_text("❌ Операция отменена")
    return ConversationHandler.END

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ℹ️ **Помощь:**\n"
        "/start - Начать создание сессии\n"
        "/help - Показать справку\n"
        "/cancel - Отменить операцию"
    )

def main():
    # Проверяем обязательные переменные
    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN не установлен!")
        return
    
    application = Application.builder().token(BOT_TOKEN).build()
    
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_phone)],
            CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_code)],
            PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_password)]
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("cancel", cancel))
    
    logger.info("🤖 Бот запущен на Railway!")
    application.run_polling()

if __name__ == '__main__':
    main()
