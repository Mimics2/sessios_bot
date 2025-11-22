import os
import logging
import asyncio
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    SessionPasswordNeededError, 
    PhoneCodeInvalidError,
    PhoneNumberInvalidError,
    FloodWaitError
)

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Конфиг - ИСПОЛЬЗУЙ СВОИ API КЛЮЧИ!
BOT_TOKEN = os.environ['BOT_TOKEN']
API_ID = int(os.environ.get('API_ID', 'YOUR_API_ID'))  # ЗАМЕНИ!
API_HASH = os.environ.get('API_HASH', 'YOUR_API_HASH')  # ЗАМЕНИ!

PHONE, CODE, PASSWORD = range(3)

class SessionManager:
    def __init__(self):
        self.active_sessions = {}
    
    async def start_session(self, phone: str, user_id: int):
        """Начинаем сессию и отправляем код"""
        try:
            logger.info(f"🔄 Starting session for {phone}")
            
            # Создаем клиента с правильными параметрами
            client = TelegramClient(
                StringSession(), 
                API_ID, 
                API_HASH,
                device_model="iPhone",
                system_version="iOS 15.0",
                app_version="8.0",
                lang_code="en",
                system_lang_code="en"
            )
            
            await client.connect()
            logger.info("✅ Client connected")
            
            # Отправляем запрос на код с правильными параметрами
            sent_code = await client.send_code_request(
                phone,
                force_sms=False  # Пытаемся отправить как код в приложение
            )
            logger.info(f"📲 Code request sent for {phone}")
            
            # Сохраняем данные сессии
            self.active_sessions[user_id] = {
                'client': client,
                'phone': phone,
                'phone_code_hash': sent_code.phone_code_hash
            }
            
            return True, "✅ Запрос кода отправлен! Проверьте:\n• Официальное приложение Telegram\n• СМС сообщения\n• Если код не пришел, попробуйте через 2-3 минуты"
            
        except PhoneNumberInvalidError:
            return False, "❌ Неверный номер телефона"
        except FloodWaitError as e:
            return False, f"❌ Слишком много запросов. Подождите {e.seconds} секунд"
        except Exception as e:
            logger.error(f"❌ Error sending code: {e}")
            return False, f"❌ Ошибка: {str(e)}"
    
    async def verify_code(self, user_id: int, code: str):
        """Проверяем код подтверждения"""
        if user_id not in self.active_sessions:
            return False, "❌ Сессия не найдена. Начните заново с /start"
        
        data = self.active_sessions[user_id]
        
        try:
            logger.info(f"🔄 Verifying code for user {user_id}")
            
            # Пытаемся войти с кодом
            await data['client'].sign_in(
                phone=data['phone'],
                code=code,
                phone_code_hash=data['phone_code_hash']
            )
            
            # Если успешно - получаем сессию
            session_string = data['client'].session.save()
            await data['client'].disconnect()
            
            # Очищаем сессию
            del self.active_sessions[user_id]
            
            logger.info("✅ Session created successfully")
            return True, session_string
            
        except SessionPasswordNeededError:
            logger.info("🔐 2FA password required")
            return False, "2FA_NEEDED"
        except PhoneCodeInvalidError:
            logger.warning("❌ Invalid code entered")
            return False, "❌ Неверный код подтверждения"
        except Exception as e:
            logger.error(f"❌ Verification error: {e}")
            await data['client'].disconnect()
            del self.active_sessions[user_id]
            return False, f"❌ Ошибка при проверке кода: {str(e)}"
    
    async def verify_password(self, user_id: int, password: str):
        """Проверяем пароль 2FA"""
        if user_id not in self.active_sessions:
            return False, "❌ Сессия не найдена"
        
        data = self.active_sessions[user_id]
        
        try:
            logger.info("🔄 Verifying 2FA password")
            await data['client'].sign_in(password=password)
            
            session_string = data['client'].session.save()
            await data['client'].disconnect()
            del self.active_sessions[user_id]
            
            return True, session_string
            
        except Exception as e:
            logger.error(f"❌ 2FA error: {e}")
            await data['client'].disconnect()
            del self.active_sessions[user_id]
            return False, f"❌ Неверный пароль 2FA"

manager = SessionManager()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начало диалога"""
    user_id = update.effective_user.id
    
    # Очищаем старые сессии
    if user_id in manager.active_sessions:
        try:
            await manager.active_sessions[user_id]['client'].disconnect()
            del manager.active_sessions[user_id]
        except:
            pass
    
    await update.message.reply_text(
        "🔐 **Telegram Session Generator**\n\n"
        "📱 Отправьте номер телефона в международном формате:\n"
        "**Пример:** +79123456789\n\n"
        "⚠️ **ВАЖНО:** Убедитесь, что номер активен в Telegram!"
    )
    return PHONE

async def handle_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка номера телефона"""
    phone = update.message.text.strip()
    user_id = update.effective_user.id
    
    # Валидация номера
    if not phone.startswith('+'):
        await update.message.reply_text("❌ Используйте международный формат, начиная с +")
        return PHONE
    
    # Показываем что бот работает
    processing_msg = await update.message.reply_text("🔄 Отправляем запрос кода...")
    
    # Отправляем код
    success, message = await manager.start_session(phone, user_id)
    
    if success:
        await processing_msg.edit_text(
            f"✅ {message}\n\n"
            f"📨 **Введите код подтверждения:**\n"
            f"(5 цифр)"
        )
        return CODE
    else:
        await processing_msg.edit_text(
            f"{message}\n\n"
            "Попробуйте снова: /start"
        )
        return ConversationHandler.END

async def handle_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка кода подтверждения"""
    code = update.message.text.replace(' ', '').replace('-', '')
    user_id = update.effective_user.id
    
    # Валидация кода
    if not code.isdigit() or len(code) < 4:
        await update.message.reply_text("❌ Код должен содержать только цифры (4-5 цифр)")
        return CODE
    
    processing_msg = await update.message.reply_text("🔄 Проверяем код...")
    
    # Проверяем код
    success, result = await manager.verify_code(user_id, code)
    
    if success:
        await processing_msg.edit_text("✅ Код верный! Создаем сессию...")
        
        # Отправляем файл с сессией
        phone = manager.active_sessions.get(user_id, {}).get('phone', 'unknown')
        
        await update.message.reply_document(
            document=result.encode('utf-8'),
            filename=f"session_{phone.replace('+', '')}.txt",
            caption=f"✅ **Сессия создана!**\n\n📱 Номер: `{phone}`",
            parse_mode='Markdown'
        )
        
        # Дублируем строку сессии
        await update.message.reply_text(
            f"📋 **Session String:**\n```\n{result}\n```",
            parse_mode='Markdown'
        )
        
    elif result == "2FA_NEEDED":
        await processing_msg.edit_text(
            "🔐 **Требуется двухфакторная аутентификация**\n\n"
            "Введите пароль 2FA:"
        )
        return PASSWORD
    else:
        await processing_msg.edit_text(
            f"{result}\n\n"
            "Попробуйте снова: /start"
        )
    
    return ConversationHandler.END

async def handle_password(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка пароля 2FA"""
    password = update.message.text
    user_id = update.effective_user.id
    
    processing_msg = await update.message.reply_text("🔄 Проверяем пароль...")
    
    success, session_string = await manager.verify_password(user_id, password)
    
    if success:
        await processing_msg.edit_text("✅ Пароль верный! Создаем сессию...")
        
        phone = manager.active_sessions.get(user_id, {}).get('phone', 'unknown')
        
        await update.message.reply_document(
            document=session_string.encode('utf-8'),
            filename=f"session_{phone.replace('+', '')}.txt",
            caption=f"✅ **Сессия создана!**\n\n📱 Номер: `{phone}`",
            parse_mode='Markdown'
        )
        
        await update.message.reply_text(
            f"📋 **Session String:**\n```\n{session_string}\n```",
            parse_mode='Markdown'
        )
    else:
        await processing_msg.edit_text(
            f"{session_string}\n\n"
            "Попробуйте снова: /start"
        )
    
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отмена операции"""
    user_id = update.effective_user.id
    
    # Очищаем сессию
    if user_id in manager.active_sessions:
        try:
            await manager.active_sessions[user_id]['client'].disconnect()
            del manager.active_sessions[user_id]
        except:
            pass
    
    await update.message.reply_text("❌ Операция отменена")
    return ConversationHandler.END

def main():
    # Проверяем токен
    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN not set!")
        return
    
    # Создаем приложение
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Conversation handler
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
    
    # Запускаем бота
    try:
        logger.info("🤖 Starting bot...")
        application.run_polling(drop_pending_updates=True)
    except Exception as e:
        logger.error(f"❌ Critical error: {e}")

if __name__ == '__main__':
    main()
