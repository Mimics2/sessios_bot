import os
import logging
import asyncio
import qrcode
from io import BytesIO
from datetime import datetime, timedelta
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
        self.session_timeouts = {}
    
    async def cleanup_old_sessions(self):
        """Очистка устаревших сессий"""
        now = datetime.now()
        expired_sessions = []
        
        for user_id, timeout in self.session_timeouts.items():
            if now > timeout:
                expired_sessions.append(user_id)
        
        for user_id in expired_sessions:
            if user_id in self.active_sessions:
                try:
                    await self.active_sessions[user_id]['client'].disconnect()
                    del self.active_sessions[user_id]
                except:
                    pass
            if user_id in self.qr_sessions:
                try:
                    await self.qr_sessions[user_id]['client'].disconnect()
                    del self.qr_sessions[user_id]
                except:
                    pass
            if user_id in self.session_timeouts:
                del self.session_timeouts[user_id]
            logger.info(f"🧹 Очищена устаревшая сессия для {user_id}")
    
    async def create_fresh_session(self, phone: str, user_id: int, method: str):
        """Создание новой свежей сессии"""
        try:
            # Очищаем старую сессию если есть
            if user_id in self.active_sessions:
                try:
                    await self.active_sessions[user_id]['client'].disconnect()
                except:
                    pass
                del self.active_sessions[user_id]
            
            # Создаем нового клиента
            client = TelegramClient(
                StringSession(), 
                API_ID, 
                API_HASH,
                device_model="iPhone",
                system_version="iOS 15.0",
                app_version="8.0"
            )
            
            await client.connect()
            logger.info(f"🔗 Новый клиент создан для {phone}")
            
            # Отправляем запрос кода
            sent_code = await client.send_code_request(phone)
            logger.info(f"📨 Код отправлен для {phone}")
            
            # Сохраняем сессию с временной меткой
            self.active_sessions[user_id] = {
                'client': client,
                'phone': phone,
                'phone_code_hash': sent_code.phone_code_hash,
                'method': method,
                'created_at': datetime.now()
            }
            
            # Устанавливаем таймаут 10 минут
            self.session_timeouts[user_id] = datetime.now() + timedelta(minutes=10)
            
            return True, "✅ Код отправлен! У вас есть 10 минут чтобы ввести код."
            
        except FloodWaitError as e:
            wait_time = e.seconds
            return False, f"❌ Слишком много запросов. Подождите {wait_time} секунд"
        except PhoneNumberInvalidError:
            return False, "❌ Неверный номер телефона"
        except Exception as e:
            logger.error(f"❌ Ошибка создания сессии: {e}")
            return False, f"❌ Ошибка: {str(e)}"
    
    # 🔥 МЕТОД 1: Автоматическая отправка кода
    async def auto_method(self, phone: str, user_id: int):
        """Автоматическая отправка кода"""
        await self.cleanup_old_sessions()
        return await self.create_fresh_session(phone, user_id, 'auto')
    
    # 🔥 МЕТОД 2: Ручной ввод кода
    async def manual_method(self, phone: str, user_id: int):
        """Ручной метод"""
        await self.cleanup_old_sessions()
        return await self.create_fresh_session(phone, user_id, 'manual')
    
    # 🔥 МЕТОД 3: QR-код
    async def qr_method(self, user_id: int):
        """Метод с QR-кодом"""
        try:
            await self.cleanup_old_sessions()
            
            # Очищаем старую QR сессию
            if user_id in self.qr_sessions:
                try:
                    await self.qr_sessions[user_id]['client'].disconnect()
                except:
                    pass
                del self.qr_sessions[user_id]
            
            client = TelegramClient(StringSession(), API_ID, API_HASH)
            await client.connect()
            
            qr_login = await client.qr_login()
            
            self.qr_sessions[user_id] = {
                'client': client,
                'qr_login': qr_login,
                'created_at': datetime.now()
            }
            
            # Таймаут 5 минут для QR
            self.session_timeouts[user_id] = datetime.now() + timedelta(minutes=5)
            
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
    
    # 🔥 УЛУЧШЕННАЯ ПРОВЕРКА КОДА
    async def verify_code(self, user_id: int, code: str):
        """Проверка кода с обновлением сессии при устаревании"""
        await self.cleanup_old_sessions()
        
        if user_id not in self.active_sessions:
            return False, "❌ Сессия не найдена или устарела. Начните с /start"
        
        data = self.active_sessions[user_id]
        
        # Проверяем не устарела ли сессия
        if datetime.now() > self.session_timeouts[user_id]:
            return False, "❌ Время сессии истекло. Начните с /start"
        
        try:
            # Очищаем код
            clean_code = code.replace(' ', '').replace('-', '').strip()
            
            logger.info(f"🔄 Проверка кода для {data['phone']}")
            
            # Пытаемся войти
            await data['client'].sign_in(
                phone=data['phone'],
                code=clean_code,
                phone_code_hash=data['phone_code_hash']
            )
            
            # Успех!
            session_string = data['client'].session.save()
            await data['client'].disconnect()
            
            # Очищаем сессии
            del self.active_sessions[user_id]
            if user_id in self.session_timeouts:
                del self.session_timeouts[user_id]
            
            logger.info("✅ Сессия успешно создана!")
            return True, session_string
            
        except PhoneCodeExpiredError:
            logger.warning("🕐 Код устарел, пробуем обновить...")
            # Пробуем запросить новый код
            try:
                # Создаем новую сессию с тем же номером
                success, message = await self.create_fresh_session(
                    data['phone'], user_id, data['method']
                )
                if success:
                    return False, "🔄 Код устарел. Новый код отправлен! Введите новый код:"
                else:
                    return False, "❌ Код устарел. Не удалось отправить новый код. Попробуйте /start"
            except Exception as e:
                return False, f"❌ Код устарел. Ошибка обновления: {str(e)}"
                
        except SessionPasswordNeededError:
            return False, "2FA_NEEDED"
        except PhoneCodeInvalidError:
            return False, "❌ Неверный код подтверждения"
        except FloodWaitError as e:
            return False, f"❌ Слишком много попыток. Подождите {e.seconds} секунд"
        except Exception as e:
            logger.error(f"❌ Ошибка входа: {e}")
            return False, f"❌ Ошибка: {str(e)}"
    
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
            
            # Очищаем
            del self.active_sessions[user_id]
            if user_id in self.session_timeouts:
                del self.session_timeouts[user_id]
            
            return True, session_string
        except Exception as e:
            await data['client'].disconnect()
            del self.active_sessions[user_id]
            if user_id in self.session_timeouts:
                del self.session_timeouts[user_id]
            return False, f"❌ Неверный пароль 2FA"
    
    # 🔥 ОЖИДАНИЕ QR-АВТОРИЗАЦИИ
    async def wait_qr_login(self, user_id: int):
        """Ожидание авторизации по QR-коду"""
        if user_id not in self.qr_sessions:
            return False, "❌ QR сессия не найдена"
        
        data = self.qr_sessions[user_id]
        
        try:
            # Ждем с таймаутом 2 минуты
            await asyncio.wait_for(data['qr_login'].wait(), timeout=120)
            
            session_string = data['client'].session.save()
            await data['client'].disconnect()
            
            # Очищаем
            del self.qr_sessions[user_id]
            if user_id in self.session_timeouts:
                del self.session_timeouts[user_id]
            
            return True, session_string
            
        except asyncio.TimeoutError:
            await data['client'].disconnect()
            if user_id in self.qr_sessions:
                del self.qr_sessions[user_id]
            if user_id in self.session_timeouts:
                del self.session_timeouts[user_id]
            return False, "❌ Время ожидания истекло. QR-код не был отсканирован."
        except Exception as e:
            await data['client'].disconnect()
            if user_id in self.qr_sessions:
                del self.qr_sessions[user_id]
            if user_id in self.session_timeouts:
                del self.session_timeouts[user_id]
            return False, f"❌ Ошибка: {str(e)}"

manager = SessionManager()

# 🔥 ГЛАВНОЕ МЕНЮ
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начало - выбор метода"""
    # Очищаем старые сессии при старте
    await manager.cleanup_old_sessions()
    
    keyboard = [
        [InlineKeyboardButton("🔐 Автоматическая отправка", callback_data="auto")],
        [InlineKeyboardButton("📱 Ручной ввод кода", callback_data="manual")],
        [InlineKeyboardButton("📷 QR-код (надежно)", callback_data="qr")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🔐 **Генератор сессий Telegram**\n\n"
        "💡 **Рекомендация:** Используйте QR-код - он не устаревает!\n\n"
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
                       "4. Подождите подтверждения...\n\n"
                       "⏳ Действует 5 минут"
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
            f"Формат: +79123456789\n\n"
            f"⏰ Код действителен 10 минут"
        )
        return PHONE

async def process_qr_login(user_id: int, message):
    """Фоновая обработка QR-логина"""
    try:
        # Ждем авторизацию
        success, result = await manager.wait_qr_login(user_id)
        
        if success:
            await message.reply_document(
                document=result.encode('utf-8'),
                filename='telegram_session.txt',
                caption="✅ **Сессия создана через QR-код!**"
            )
            await message.reply_text(f"`{result}`", parse_mode='Markdown')
        else:
            await message.reply_text(f"{result}\n\nПопробуйте снова: /start")
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
    
    await update.message.reply_text("🔄 Создаем свежую сессию...")
    
    if method == 'auto':
        success, message = await manager.auto_method(phone, user_id)
    else:
        success, message = await manager.manual_method(phone, user_id)
    
    if success:
        await update.message.reply_text(
            f"✅ {message}\n\n"
            f"🔢 **Введите код:**\n"
            f"• Только цифры (5 цифр)\n" 
            f"• Без пробелов и дефисов\n"
            f"• Пример: 12345"
        )
        return CODE
    else:
        await update.message.reply_text(
            f"{message}\n\n"
            f"💡 Попробуйте QR-код - он надежнее: /start"
        )
        return ConversationHandler.END

async def handle_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка кода подтверждения"""
    code = update.message.text
    user_id = update.effective_user.id
    
    logger.info(f"📨 Получен код от пользователя {user_id}")
    
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
        await update.message.reply_text("🔐 Введите пароль двухфакторной аутентификации:")
        return PASSWORD
    else:
        await update.message.reply_text(
            f"{result}\n\n"
            f"💡 **Советы:**\n"
            f"• Используйте QR-код (/start)\n"
            f"• Вводите код быстро\n"
            f"• Проверьте правильность номера"
        )
    
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
    
    if user_id in manager.session_timeouts:
        del manager.session_timeouts[user_id]
    
    await update.message.reply_text("❌ Операция отменена")
    return ConversationHandler.END

def main():
    # Создаем приложение
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Conversation handler с исправленными настройками
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            METHOD: [CallbackQueryHandler(handle_method, pattern='^(auto|manual|qr)$')],
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_phone)],
            CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_code)],
            PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_password)]
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        per_message=False  # Явно указываем настройку
    )
    
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", lambda u, c: u.message.reply_text("Используйте /start")))
    
    # Запускаем периодическую очистку через JobQueue
    async def cleanup_job(context: ContextTypes.DEFAULT_TYPE):
        await manager.cleanup_old_sessions()
    
    # Добавляем job для очистки каждые 2 минуты
    application.job_queue.run_repeating(cleanup_job, interval=120, first=10)
    
    logger.info("🤖 Бот запущен с исправленной системой очистки!")
    application.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
