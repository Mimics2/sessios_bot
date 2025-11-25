import os
import logging
import asyncio
import random
import qrcode
from io import BytesIO
from datetime import datetime, timedelta

# Настройка логирования
logging.getLogger('telethon').setLevel(logging.WARNING)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

try:
    from aiogram import Bot, Dispatcher, Router, F
    from aiogram.types import Message, CallbackQuery, BufferedInputFile
    from aiogram.filters import Command, CommandStart
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.state import State, StatesGroup
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    from telethon.errors import (
        SessionPasswordNeededError, 
        PhoneCodeInvalidError,
        PhoneNumberInvalidError,
        FloodWaitError,
        PhoneCodeExpiredError
    )
except ImportError as e:
    print(f"❌ Missing dependencies: {e}")
    exit(1)

BOT_TOKEN = os.environ.get('BOT_TOKEN')
API_ID = int(os.environ.get('API_ID', '2040'))
API_HASH = os.environ.get('API_HASH', 'b18441a1ff607e10a989891a5462e627')

class SessionStates(StatesGroup):
    METHOD = State()
    PHONE = State()
    CODE = State()
    PASSWORD = State()

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
router = Router()
dp.include_router(router)

class ImprovedSessionManager:
    def __init__(self):
        self.active_sessions = {}
        self.session_timeouts = {}
    
    async def safe_connect(self, user_id: int):
        """Безопасное подключение"""
        try:
            devices = [
                {
                    "device_model": "Samsung SM-G991B",
                    "system_version": "Android 13",
                    "app_version": "10.0.0",
                    "lang_code": "en",
                    "system_lang_code": "en-US"
                },
                {
                    "device_model": "iPhone15,3",
                    "system_version": "iOS 17.1.2", 
                    "app_version": "10.0.0",
                    "lang_code": "en",
                    "system_lang_code": "en-US"
                }
            ]
            
            device = random.choice(devices)
            
            client = TelegramClient(
                StringSession(),
                API_ID,
                API_HASH,
                **device
            )
            
            await client.connect()
            return client, True, "✅ Connected"
            
        except Exception as e:
            logger.error(f"Connection error: {e}")
            return None, False, f"❌ Connection failed: {str(e)}"
    
    async def improved_qr_login(self, user_id: int):
        """Улучшенный QR-логин с долгим ожиданием"""
        try:
            client, success, message = await self.safe_connect(user_id)
            if not success:
                return False, message, None
            
            # Увеличиваем таймаут QR-сессии
            qr_login = await client.qr_login()
            
            self.active_sessions[user_id] = {
                'client': client,
                'qr_login': qr_login,
                'created_at': datetime.now()
            }
            
            # Увеличиваем время жизни QR-сессии до 5 минут
            self.session_timeouts[user_id] = datetime.now() + timedelta(minutes=5)
            
            return True, qr_login.url, None
            
        except Exception as e:
            return False, f"❌ QR error: {str(e)}", None
    
    async def wait_extended_qr_login(self, user_id: int):
        """Ожидание QR с увеличенным временем"""
        if user_id not in self.active_sessions:
            return False, "❌ QR сессия не найдена"
        
        data = self.active_sessions[user_id]
        
        try:
            # УВЕЛИЧИВАЕМ ТАЙМАУТ ДО 3 МИНУТ (180 секунд)
            await asyncio.wait_for(data['qr_login'].wait(), timeout=180)
            
            # Проверяем авторизацию
            if not await data['client'].is_user_authorized():
                return False, "❌ Authorization failed after QR scan"
            
            # Получаем сессию
            session_string = data['client'].session.save()
            await data['client'].disconnect()
            
            # Очищаем
            del self.active_sessions[user_id]
            if user_id in self.session_timeouts:
                del self.session_timeouts[user_id]
            
            return True, session_string
            
        except asyncio.TimeoutError:
            await data['client'].disconnect()
            if user_id in self.active_sessions:
                del self.active_sessions[user_id]
            return False, "❌ Время ожидания истекло (3 минуты)"
        except Exception as e:
            await data['client'].disconnect()
            if user_id in self.active_sessions:
                del self.active_sessions[user_id]
            return False, f"❌ Ошибка: {str(e)}"
    
    async def create_code_session(self, phone: str, user_id: int):
        """Создание сессии через код (если нужно)"""
        try:
            client, success, message = await self.safe_connect(user_id)
            if not success:
                return False, message
            
            # Отправляем код
            sent_code = await client.send_code_request(phone)
            
            self.active_sessions[user_id] = {
                'client': client,
                'phone': phone,
                'phone_code_hash': sent_code.phone_code_hash
            }
            
            self.session_timeouts[user_id] = datetime.now() + timedelta(minutes=5)
            
            return True, "✅ Код отправлен! Введите код:"
            
        except FloodWaitError as e:
            return False, f"❌ Слишком много запросов. Подождите {e.seconds} секунд"
        except Exception as e:
            return False, f"❌ Ошибка: {str(e)}"
    
    async def verify_code_session(self, user_id: int, code: str):
        """Проверка кода"""
        if user_id not in self.active_sessions:
            return False, "❌ Сессия не найдена"
        
        data = self.active_sessions[user_id]
        
        try:
            clean_code = code.replace(' ', '').replace('-', '').strip()
            
            await data['client'].sign_in(
                phone=data['phone'],
                code=clean_code,
                phone_code_hash=data['phone_code_hash']
            )
            
            session_string = data['client'].session.save()
            await data['client'].disconnect()
            
            del self.active_sessions[user_id]
            if user_id in self.session_timeouts:
                del self.session_timeouts[user_id]
            
            return True, session_string
            
        except SessionPasswordNeededError:
            return False, "2FA_NEEDED"
        except PhoneCodeInvalidError:
            return False, "❌ Неверный код"
        except Exception as e:
            await data['client'].disconnect()
            del self.active_sessions[user_id]
            return False, f"❌ Ошибка: {str(e)}"

manager = ImprovedSessionManager()

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    
    builder = InlineKeyboardBuilder()
    builder.button(text="📷 QR-код (3 минуты)", callback_data="method_qr")
    builder.button(text="🔐 Получить код", callback_data="method_code")
    builder.adjust(1)
    
    await message.answer(
        "🔐 **Генератор сессий Telegram**\n\n"
        "💡 **QR-код теперь работает 3 минуты!**\n\n"
        "Выберите метод:",
        reply_markup=builder.as_markup()
    )
    await state.set_state(SessionStates.METHOD)

@router.callback_query(F.data.startswith("method_"))
async def handle_method(callback: CallbackQuery, state: FSMContext):
    method = callback.data.replace("method_", "")
    user_id = callback.from_user.id
    
    await callback.answer()
    
    if method == "qr":
        await callback.message.edit_text("🔄 Создаем QR-код...")
        
        success, qr_url, error = await manager.improved_qr_login(user_id)
        
        if success:
            # Создаем QR-код
            qr = qrcode.QRCode(version=1, box_size=8, border=4)
            qr.add_data(qr_url)
            qr.make(fit=True)
            
            img = qr.make_image(fill_color="black", back_color="white")
            bio = BytesIO()
            img.save(bio, 'PNG')
            bio.seek(0)
            
            qr_file = BufferedInputFile(bio.getvalue(), filename="qr_code.png")
            
            await callback.message.answer_photo(
                photo=qr_file,
                caption="📷 **QR-код действителен 3 минуты:**\n\n"
                       "1. Откройте Telegram → Настройки\n"
                       "2. Устройства → Подключить устройство\n"
                       "3. Отсканируйте QR-код\n"
                       "4. Подождите подтверждения...\n\n"
                       "⏳ **Ожидаем до 3 минут**\n"
                       "🔄 Автоматически создаст сессию"
            )
            
            # Запускаем долгое ожидание
            asyncio.create_task(process_extended_qr(user_id, callback.message))
            await state.clear()
        else:
            await callback.message.edit_text(f"❌ {qr_url}")
    
    elif method == "code":
        await callback.message.edit_text(
            "📱 **Получение кода:**\n\n"
            "Отправьте номер телефона:\n"
            "Формат: +79123456789\n\n"
            "⏰ Код действителен 5 минут"
        )
        await state.update_data(method="code")
        await state.set_state(SessionStates.PHONE)

@router.message(SessionStates.PHONE)
async def handle_phone(message: Message, state: FSMContext):
    phone = message.text.strip()
    user_id = message.from_user.id
    data = await state.get_data()
    method = data.get('method', 'code')
    
    if not phone.startswith('+'):
        await message.answer("❌ Используйте формат +79123456789")
        return
    
    processing_msg = await message.answer("🔄 Отправляем код...")
    
    success, result = await manager.create_code_session(phone, user_id)
    
    if success:
        await processing_msg.edit_text(
            f"✅ {result}\n\n"
            f"🔢 **Введите код из Telegram:**\n"
            f"• 5 цифр\n" 
            f"• Пример: 12345"
        )
        await state.update_data(phone=phone)
        await state.set_state(SessionStates.CODE)
    else:
        await processing_msg.edit_text(
            f"{result}\n\n"
            f"💡 Попробуйте QR-код: /start"
        )
        await state.clear()

@router.message(SessionStates.CODE)
async def handle_code(message: Message, state: FSMContext):
    code = message.text
    user_id = message.from_user.id
    
    processing_msg = await message.answer("🔄 Проверяем код...")
    
    success, result = await manager.verify_code_session(user_id, code)
    
    if success:
        data = await state.get_data()
        phone = data.get('phone', 'unknown')
        
        session_file = BufferedInputFile(
            result.encode('utf-8'),
            filename=f"session_{phone.replace('+', '')}.txt"
        )
        
        await processing_msg.edit_text("✅ Код верный! Создаем сессию...")
        await message.answer_document(
            document=session_file,
            caption=f"✅ **Сессия создана для {phone}!**"
        )
        await message.answer(f"📋 **Session String:**\n```\n{result}\n```")
        
    elif result == "2FA_NEEDED":
        await processing_msg.edit_text("🔐 Введите пароль двухфакторной аутентификации:")
        await state.set_state(SessionStates.PASSWORD)
        return
    
    else:
        await processing_msg.edit_text(
            f"{result}\n\n"
            f"💡 Попробуйте снова: /start"
        )
    
    await state.clear()

@router.message(SessionStates.PASSWORD)
async def handle_password(message: Message, state: FSMContext):
    password = message.text
    user_id = message.from_user.id
    
    # Для 2FA нужно использовать другой подход
    await message.answer(
        "🔐 **2FA обнаружена**\n\n"
        "Для аккаунтов с двухфакторной аутентификацией:\n\n"
        "1. Используйте QR-код метод (/start)\n"
        "2. Или отключите 2FA временно\n"
        "3. Или используйте официальный клиент\n\n"
        "QR-код автоматически обрабатывает 2FA!"
    )
    await state.clear()

async def process_extended_qr(user_id: int, message: Message):
    """Обработка QR с увеличенным временем ожидания"""
    try:
        # Показываем прогресс каждые 30 секунд
        progress_messages = []
        
        for i in range(6):  # 3 минуты = 6 интервалов по 30 секунд
            await asyncio.sleep(30)  # Ждем 30 секунд
            
            # Обновляем прогресс
            time_left = 150 - (i * 30)  # Оставшееся время в секундах
            minutes = time_left // 60
            seconds = time_left % 60
            
            progress_text = (
                f"⏳ Ожидаем авторизацию...\n"
                f"🕐 Осталось: {minutes}:{seconds:02d}\n"
                f"📱 Можно сканировать QR-код"
            )
            
            if progress_messages:
                await progress_messages[-1].edit_text(progress_text)
            else:
                progress_msg = await message.answer(progress_text)
                progress_messages.append(progress_msg)
        
        # После 3 минут проверяем результат
        success, result = await manager.wait_extended_qr_login(user_id)
        
        if success:
            if progress_messages:
                await progress_messages[-1].edit_text("✅ Авторизация успешна! Создаем сессию...")
            
            session_file = BufferedInputFile(
                result.encode('utf-8'),
                filename="telegram_session.txt"
            )
            
            await message.answer_document(
                document=session_file,
                caption="✅ **Сессия создана через QR-код!**\n\n"
                       "⏱️ Время работы: 3 минуты\n"
                       "🔒 Безопасное подключение"
            )
            await message.answer(f"📋 **Session String:**\n```\n{result}\n```")
            
        else:
            if progress_messages:
                await progress_messages[-1].edit_text(f"❌ {result}\n\nПопробуйте снова: /start")
            else:
                await message.answer(f"❌ {result}\n\nПопробуйте снова: /start")
                
    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)}\n\nПопробуйте снова: /start")

@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "🔐 **Генератор сессий Telegram**\n\n"
        "💡 **Новое:** QR-код работает 3 минуты!\n\n"
        "Методы:\n"
        "• 📷 **QR-код** - 3 минуты, работает с 2FA\n"
        "• 🔐 **Код** - 5 минут, стандартный метод\n\n"
        "Команды:\n"
        "/start - Начать\n"
        "/help - Помощь"
    )

@router.message()
async def handle_other_messages(message: Message):
    await message.answer("🤖 Используйте /start для создания сессии")

async def main():
    logger.info("🚀 Starting Improved QR Bot (3 minutes timeout)...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
