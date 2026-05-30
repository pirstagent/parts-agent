import os, logging, base64, json, re, requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CLAUDE_API_KEY = os.environ.get("CLAUDE_API_KEY")

user_sessions = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "გამარჯობა ზურაბ! მე ვარ eBay ავტონაწილების ასისტენტი.\n\n"
        "გამომიგზავნე Part Number ან ფოტო და მე მომენტალურად "
        "მოგიმზადებ გამზადებულ სათაურს, აღწერას და თავსებადობის სიას eBay-ზე ხელით ჩასასმელად!"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text or ""
    if user_id not in user_sessions: user_sessions[user_id] = {"part_number": None, "photos": []}
    if text: user_sessions[user_id]["part_number"] = text.strip()
    await update.message.reply_text("Part number მიღებულია! გამომიგზავნე ფოტო ან დაწერე /process")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_sessions: user_sessions[user_id] = {"part_number": None, "photos": []}
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    photo_bytes = await file.download_as_bytearray()
    photo_b64 = base64.b64encode(photo_bytes).decode("utf-8")
    user_sessions[user_id]["photos"].append(photo_b64)
    if update.message.caption: user_sessions[user_id]["part_number"] = update.message.caption.strip()
    
    part = user_sessions[user_id]["part_number"] or "არ არის"
    keyboard = [[InlineKeyboardButton("მონაცემების მომზადება", callback_data="process")]]
    await update.message.reply_text(
        f"ფოტო მიღებულია!\nPart Number: {part}\n\nდააჭირე მონაცემების მომზადებას:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def process_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("AI ეძებს ნაწილის დეტალებს ინტერნეტში...")
    await process_listing(update.callback_query.from_user.id, update.callback_query.message)

async def process_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("AI ეძებს ნაწილის დეტალებს ინტერნეტში...")
    await process_listing(update.effective_user.id, update.message)

async def process_listing(user_id, message):
    session = user_sessions.get(user_id, {})
    part_number = session.get("part_number", "")
    photos = session.get("photos", [])
    if not part_number and not photos:
        await message.reply_text("გამომიგზავნე Part Number ან ფოტო პირველ რიგში!")
        return
    try:
        listing = await call_claude(part_number, photos)
        user_sessions[user_id] = {"part_number": None, "photos": []}
        compat_list = listing.get("compatibility", [])
        compat_text = "\n".join("- " + c for c in compat_list)
        
        # ვაბრუნებთ სუფთა მონაცემებს პირდაპირ კოპირებისთვის
        result = (
            f"📋 **eBay განცხადების შაბლონი მზად არის!**\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📌 **სათაური (დააკოპირე):**\n`{listing.get('title', '')}`\n\n"
            f"💰 **რეკომენდებული ფასი:** ${listing.get('suggested_price', '25')}\n"
            f"🏷️ **ბრენდი:** {listing.get('brand', 'Unbranded')}\n"
            f"🔢 **Part Number:** {listing.get('part_number', 'N/A')}\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🚗 **თავსებადი მანქანები:**\n{compat_text}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📝 **აღწერა (Description):**\n\n{listing.get('description', '')}\n\n"
            f"ℹ️ *მდგომარეობის აღწერა:* {listing.get('condition_description', '')}"
        )
        await message.reply_text(result, parse_mode="Markdown")
    except Exception as e:
        logger.error("Error: " + str(e))
        await message.reply_text(f"შეცდომა: {str(e)}\n\nსცადე /start")

async def call_claude(part_number, photos):
    headers = {"x-api-key": CLAUDE_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    content = []
    for photo_b64 in photos[:4]:
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": photo_b64}})
    prompt = (
        f"You are an expert eBay auto parts specialist. Analyze this auto part.\nPart Number: {part_number or 'See image'}\n\n"
        "Provide complete eBay listing details. Use category_id 262 for Parts and Accessories.\n"
        "Respond ONLY with valid JSON, no markdown:\n"
        "{\n"
        '  "title": "eBay title max 80 chars SEO optimized with part number",\n'
        '  "description": "Detailed description plain text for eBay listing",\n'
        '  "suggested_price": 25,\n'
        '  "compatibility": ["Year Make Model", "Year Make Model"],\n'
        '  "brand": "Car Brand or Mopar/Toyota etc",\n'
        '  "part_number": "exact part number",\n'
        '  "condition_description": "Good working condition OEM part"\n'
        "}"
    )
    content.append({"type": "text", "text": prompt})
    response = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers=headers,
        json={"model": "claude-sonnet-4-6", "max_tokens": 1500, "messages": [{"role": "user", "content": content}]},
        timeout=60
    )
    data = response.json()
    if "error" in data: raise ValueError("Claude API error: " + str(data["error"]))
    text = "".join([block.get("text", "") for block in data.get("content", []) if block.get("type") == "text"]).strip()
    start = text.find("{")
    end = text.rfind("}") + 1
    return json.loads(text[start:end]) if (start >= 0 and end > start) else json.loads(text)

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("process", process_command))
    app.add_handler(CallbackQueryHandler(process_callback, pattern="process"))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Bot started!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
