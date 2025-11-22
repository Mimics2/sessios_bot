import os
import logging
import asyncio
import qrcode
from io import BytesIO
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, 
    ContextTypes, ConversationHandler, CallbackQueryHandler
)
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    SessionPasswordNeededError, 
    PhoneCodeInvalidError,
    PhoneNumberInvalidError,
    FloodWaitError,
    PhoneCodeExpiredError
)

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Конфиг
BOT_TOKEN = os.environ['BOT_TOKEN']
API_ID = int(os.environ.get('API_ID', '2040'))
API_HASH = os.environ.get('API_HASH', 'b18441a1ff607e10a989891a5462e627')

# Состояния
METHOD, PHONE, CODE, PASSWORD = range(4)

class SessionManager:
    def __init__(self):
        self.active_sessions = {}
        self.qr_sessions = {}
    
    # 🔥 МЕТОД 1: Автоматическая отправка кода
    async def auto_method(self, phone: str, user_id: int):
        """Автоматическая отправка кода"""
        try:
            client = TelegramClient(StringSession(), API_ID, API_HASH)
            await client.connect()
            
            sent_code = await client.send_code_request(phone)
            
            self.active_sessions[user_id] = {
                'client': client,
                'phone': phone,
                'phone_code_hash': sent_code.phone_code_hash,
                'method': 'auto'
            }
            
            return True, "✅ Запрос кода отправлен! Проверьте Telegram."
            
        except FloodWaitError as e:
            return False, f"❌ Слишком много запросов. Подождите {e.seconds} секунд"
        except PhoneNumberInvalidError:
            return False, "❌ Неверный номер телефона"
        except Exception as e:
            logger.error(f"Auto method error: {e}")
            return False, f"❌ Ошибка: {str(e)}"
    
    # 🔥 МЕТОД 2: Ручной ввод кода
    async def manual_method(self, phone: str, user_id: int):
        """Ручной метод"""
        try:
            client = TelegramClient(StringSession(), API_ID, API_HASH)
            await client.connect()
            
            sent_code = await client.send_code_request(phone)
            
            self.active_sessions[user_id] = {
                'client': client,
                'phone': phone,
                'phone_code_hash': sent_code.phone_code_hash,
                'method': 'manual'
            }
            
            return True, (
                "📱 **Ручной метод:**\n\n"
                "Код отправлен в Telegram. Введите код:\n\n"
                "🔢 **Форматы:** 12345 или 12-345"
            )
        except Exception as e:
            return False, f"❌ Ошибка: {str(e)}"
    
    # 🔥 МЕТОД 3: QR-код
    async def qr_method(self, user_id: int):
        """Метод с QR-кодом"""
        try:
            client = TelegramClient(StringSession(), API_ID, API_HASH)
            await client.connect()
            
            qr_login = await client.qr_login()
            
            self.qr_sessions[user_id] = {
                'client': client,
                'qr_login': qr_login
            }
            
            # Создаем QR-код
            qr = qrcode.QRCode(version=1, box_size=10, border=5)
            qr.add_data(qr_login.url)
            qr.make(fit=True)
            
            img = qr.make_image(fill_color="black", back_color="white")
            bio = BytesIO()
            img.save(bio, 'PNG')
            bio.seek(0)
            
            return True, bio, qr_login
            
        except Exception as e:
            return False, f"❌ Ошибка QR-метода: {str(e)}", None
    
    # 🔥 ИСПРАВЛЕННАЯ ПРОВЕРКА КОДА
    async def verify_code(self, user_id: int, code: str):
        """Проверка кода с улучшенной обработкой"""
        if user_id not in self.active_sessions:
            return False, "❌ Сессия не найдена. Начните с /start"
        
        data = self.active_sessions[user_id]
        
        try:
            # Очищаем код от лишних символов
            clean_code = code.replace(' ', '').replace('-', '').strip()
            
            logger.info(f"🔄 Проверка кода: {clean_code} для {data['phone']}")
            
            # Пытаемся войти
            await data['client'].sign_in(
                phone=data['phone'],
                code=clean_code,
                phone_code_hash=data['phone_code_hash']
            )
            
            # Успешная авторизация
            session_string = data['client'].session.save()
            await data['client'].disconnect()
            del self.active_sessions[user_id]
            
            logger.info("✅ Код верный, сессия создана")
            return True, session_string
            
        except SessionPasswordNeededError:
            logger.info("🔐 Требуется 2FA пароль")
            return False, "2FA_NEEDED"
        except PhoneCodeInvalidError:
            logger.warning("❌ Неверный код")
            return False, "❌ Неверный код подтверждения"
        except PhoneCodeExpiredError:
            logger.warning("❌ Код устарел")
            return False, "❌ Код устарел. Запросите новый код с /start"
        except FloodWaitError as e:
            logger.warning(f"⏳ Flood wait: {e.seconds} сек")
            return False, f"❌ Слишком много попыток. Подождите {e.seconds} секунд"
        except Exception as e:
            logger.error(f"❌ Ошибка проверки кода: {e}")
            # Пробуем альтернативный метод
            return await self.try_alternative_login(user_id, clean_code)
    
    # 🔥 АЛЬТЕРНАТИВНЫЙ МЕТОД ВХОДА
    async def try_alternative_login(self, user_id: int, code: str):
        """Альтернативный метод входа"""
        if user_id not in self.active_sessions:
            return False, "❌ Сессия не найдена"
        
        data = self.active_sessions[user_id]
        
        try:
            # Пробуем метод start вместо sign_in
            await data['client'].start(
                phone=lambda: data['phone'],
                code=lambda: code,
                password=None
            )
            
            session_string = data['client'].session.save()
            await data['client'].disconnect()
            del self.active_sessions[user_id]
            
            return True, session_string
        except Exception as e:
            await data['client'].disconnect()
            del self.active_sessions[user_id]
            return False, f"❌ Ошибка входа: {str(e)}"
    
    # 🔥 ПРОВЕРКА ПАРОЛЯ 2FA
    async def verify_password(self, user_id: int, password: str):
        """Проверка пароля 2FA"""
        if user_id not in self.active_sessions:
            return False, "❌ Сессия не найдена"
        
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
            return False, f"❌ Неверный пароль 2FA"
    
    # 🔥 ОЖИДАНИЕ QR-АВТОРИЗАЦИИ
    async def wait_qr_login(self, user_id: int):
        """Ожидание авторизации по QR-коду"""
        if user_id not in self.qr_sessions:
            return False, "❌ QR сессия не найдена"
        
        data = self.qr_sessions[user_id]
        
        try:
            await asyncio.wait_for(data['qr_login'].wait(), timeout=120)
            session_string = data['client'].session.save()
            await data['client'].disconnect()
            del self.qr_sessions[user_id]
            
            return True, session_string
            
        except asyncio.TimeoutError:
            await data['client'].disconnect()
            del self.qr_sessions[user_id]
            return False, "❌ Время ожидания истекло"
        except Exception as e:
            await data['client'].disconnect()
            del self.qr_sessions[user_id]
            return False, f"❌ Ошибка QR-авторизации: {str(e)}"

manager = SessionManager()

# 🔥 ГЛАВНОЕ МЕНЮ
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начало - выбор метода"""
    keyboard = [
        [InlineKeyboardButton("🔐 Автоматическая отправка", callback_data="auto")],
        [InlineKeyboardButton("📱 Ручной ввод кода", callback_data="manual")],
        [InlineKeyboardButton("📷 QR-код (рекомендуется)", callback_data="qr")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🔐 **Генератор сессий Telegram**\n\n"
        "💡 **Если код не приходит или не работает - используйте QR-код!**\n\n"
        "Выберите способ:",
        reply_markup=reply_markup
    )
    return METHOD

async def handle_method(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка выбора метода"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    method = query.data
    
    context.user_data['method'] = method
    
    if method == 'qr':
        await query.edit_message_text("🔄 Генерируем QR-код...")
        
        success, qr_image, qr_login = await manager.qr_method(user_id)
        
        if success:
            await query.message.reply_photo(
                photo=qr_image,
                caption="📷 **Вход по QR-коду:**\n\n"
                       "1. Откройте Telegram → Настройки\n"
                       "2. Устройства → Подключить устройство\n" 
                       "3. Отсканируйте QR-код\n"
                       "4. Подождите...\n\n"
                       "⏳ Ожидаем ~2 минуты"
            )
            
            # Фоновая обработка QR
            asyncio.create_task(process_qr_login(user_id, query.message))
            return ConversationHandler.END
        else:
            await query.message.reply_text(qr_image)
            return ConversationHandler.END
    
    else:
        method_name = "автоматической отправки" if method == 'auto' else "ручного ввода"
        await query.edit_message_text(
            f"📱 **Метод {method_name}**\n\n"
            f"Отправьте номер телефона:\n"
            f"Формат: +79123456789"
        )
        return PHONE

async def process_qr_login(user_id: int, message):
    """Фоновая обработка QR-логина"""
    try:
        # Даем время на сканирование
        await asyncio.sleep(5)
        
        success, result = await manager.wait_qr_login(user_id)
        
        if success:
            await message.reply_document(
                document=result.encode('utf-8'),
                filename='telegram_session.txt',
                caption="✅ **Сессия создана через QR-код!**"
            )
            await message.reply_text(f"`{result}`", parse_mode='Markdown')
        else:
            await message.reply_text(f"❌ {result}\n\nПопробуйте /start")
    except Exception as e:
        await message.reply_text(f"❌ Ошибка: {str(e)}")

async def handle_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка номера телефона"""
    phone = update.message.text.strip()
    user_id = update.effective_user.id
    method = context.user_data.get('method', 'auto')
    
    if not phone.startswith('+'):
        await update.message.reply_text("❌ Используйте формат +79123456789")
        return PHONE
    
    await update.message.reply_text("🔄 Отправляем запрос...")
    
    if method == 'auto':
        success, message = await manager.auto_method(phone, user_id)
    else:
        success, message = await manager.manual_method(phone, user_id)
    
    if success:
        await update.message.reply_text(
            f"✅ {message}\n\n"
            f"🔢 **Введите код:**\n"
            f"• Только цифры\n" 
            f"• Без пробелов\n"
            f"• Пример: 12345"
        )
        return CODE
    else:
        await update.message.reply_text(f"{message}\n\nПопробуйте QR-код: /start")
        return ConversationHandler.END

async def handle_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка кода подтверждения"""
    code = update.message.text
    user_id = update.effective_user.id
    
    logger.info(f"📨 Получен код: {code} от пользователя {user_id}")
    
    if not any(char.isdigit() for char in code):
        await update.message.reply_text("❌ Код должен содержать цифры")
        return CODE
    
    await update.message.reply_text("🔄 Проверяем код...")
    
    success, result = await manager.verify_code(user_id, code)
    
    if success:
        phone = manager.active_sessions.get(user_id, {}).get('phone', 'unknown')
        
        await update.message.reply_document(
            document=result.encode('utf-8'),
            filename=f'session_{phone.replace("+", "")}.txt',
            caption="✅ **Сессия создана!**"
        )
        await update.message.reply_text(f"`{result}`", parse_mode='Markdown')
        
    elif result == "2FA_NEEDED":
        await update.message.reply_text("🔐 Введите пароль 2FA:")
        return PASSWORD
    else:
        # Подробная информация об ошибке
        error_msg = result + "\n\n💡 **Попробуйте:**\n• Проверить код\n• Использовать QR-код\n• Подождать 2 минуты"
        await update.message.reply_text(error_msg)
    
    return ConversationHandler.END

async def handle_password(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка пароля 2FA"""
    password = update.message.text
    user_id = update.effective_user.id
    
    await update.message.reply_text("🔄 Проверяем пароль...")
    
    success, session_string = await manager.verify_password(user_id, password)
    
    if success:
        phone = manager.active_sessions.get(user_id, {}).get('phone', 'unknown')
        
        await update.message.reply_document(
            document=session_string.encode('utf-8'),
            filename=f'session_{phone.replace("+", "")}.txt',
            caption="✅ **Сессия создана!**"
        )
        await update.message.reply_text(f"`{session_string}`", parse_mode='Markdown')
    else:
        await update.message.reply_text(f"{session_string}\n\nПопробуйте /start")
    
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отмена"""
    user_id = update.effective_user.id
    
    if user_id in manager.active_sessions:
        try:
            await manager.active_sessions[user_id]['client'].disconnect()
            del manager.active_sessions[user_id]
        except: pass
    
    if user_id in manager.qr_sessions:
        try:
            await manager.qr_sessions[user_id]['client'].disconnect()
            del manager.qr_sessions[user_id]
        except: pass
    
    await update.message.reply_text("❌ Отменено")
    return ConversationHandler.END

def main():
    application = Application.builder().token(BOT_TOKEN).build()
    
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            METHOD: [CallbackQueryHandler(handle_method)],
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_phone)],
            CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_code)],
            PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_password)]
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("start", start))
    
    logger.info("🤖 Бот запущен с улучшенной проверкой кодов!")
    application.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
