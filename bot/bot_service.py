"""
Telegram Bot Service implementation using python-telegram-bot.
"""
import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from accounts.models import User
from .services import (
    identify_worker,
    scan_ticket_item,
    finalize_and_route,
    clear_session,
    get_or_create_session
)

logger = logging.getLogger(__name__)


def get_master_user(telegram_user_id: int):
    try:
        return User.objects.filter(telegram_user_id=telegram_user_id).first()
    except Exception:
        return None


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    clear_session(user_id)

    text = (
        "👋 <b>Assalomu alaykum, Hurmatli Master!</b>\n\n"
        "Ushbu bot tikuvchilarning operatsiya QR biletlarini qabul qilish "
        "va sex ekranlariga (1–10) yo'naltirish uchun xizmat qiladi.\n\n"
        "👉 <b>1-qadam:</b> Tikuvchining shaxsiy QR nishonini skanerlang "
        "yoki ID raqamini kiriting (Masalan: <code>W-001</code>):"
    )
    await update.message.reply_html(text)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    text = (update.message.text or '').strip()

    session = get_or_create_session(user_id)

    # 1-holat: Hali xodim tanlanmagan
    if not session or not session.get('worker_id'):
        success, reply_msg, worker = identify_worker(user_id, text)
        if success:
            keyboard = [
                [InlineKeyboardButton("❌ Bekor qilish", callback_data="CANCEL")]
            ]
            await update.message.reply_html(
                reply_msg, 
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            await update.message.reply_html(reply_msg)
        return

    # 2-holat: Xodim tanlangan, biletlar skanerlanmoqda
    success, reply_msg, data = scan_ticket_item(user_id, text)
    if success:
        keyboard = [
            [
                InlineKeyboardButton(
                    f"✅ YAKUNLASH ({data['total_tickets_count']} ta bilet)", 
                    callback_data="ASK_SCREEN"
                )
            ],
            [
                InlineKeyboardButton("❌ Bekor qilish", callback_data="CANCEL")
            ]
        ]
        await update.message.reply_html(
            reply_msg, 
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        await update.message.reply_html(reply_msg)


async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = str(query.from_user.id)
    data = query.data

    if data == "CANCEL":
        clear_session(user_id)
        await query.edit_message_text(
            "❌ Amal bekor qilindi.\nYangi xodimning QR nishonini yoki ID raqamini yuboring:"
        )
        return

    if data == "ASK_SCREEN":
        session = get_or_create_session(user_id)
        if not session or not session.get('pending_ticket_ids'):
            await query.edit_message_text("⚠️ Skanerlangan biletlar yo'q!")
            return

        # 1 dan 10 gacha bo'lgan ekranlar tugmalari (2 qatorda 5 tadan)
        row1 = [InlineKeyboardButton(f"📺 {i}", callback_data=f"SCREEN_{i}") for i in range(1, 6)]
        row2 = [InlineKeyboardButton(f"📺 {i}", callback_data=f"SCREEN_{i}") for i in range(6, 11)]
        row3 = [InlineKeyboardButton("❌ Bekor qilish", callback_data="CANCEL")]

        keyboard = InlineKeyboardMarkup([row1, row2, row3])
        await query.edit_message_text(
            f"📺 <b>Qaysi ekranga yo'naltirilsin? (1–10)</b>\n"
            f"Xodim: <b>{session.get('worker_name')}</b>\n"
            f"Biletlar: <b>{len(session.get('pending_ticket_ids', []))} ta</b>",
            parse_mode="HTML",
            reply_markup=keyboard
        )
        return

    if data.startswith("SCREEN_"):
        screen_num = int(data.split("_")[1])
        master = get_master_user(query.from_user.id)

        success, msg, summary = finalize_and_route(user_id, screen_num, master)
        if success:
            await query.edit_message_text(msg, parse_mode="HTML")
        else:
            await query.edit_message_text(msg, parse_mode="HTML")


def create_bot_app(token: str):
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(handle_callback_query))
    return app

