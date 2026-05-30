import os, logging, base64, json, re, requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CLAUDE_API_KEY = os.environ.get("CLAUDE_API_KEY")
EBAY_OAUTH_TOKEN = os.environ.get("EBAY_OAUTH_TOKEN")

user_sessions = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "გამარჯობა! მე ვარ eBay ავტონაწილების ახალი აგენტი.\n\n"
        "გამომიგზავნე Part Number ან ფოტო და მე გარანტირებულად შევინახავ დრაფტებში!"
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
    keyboard = [[InlineKeyboardButton("დამუშავება", callback_data="process")]]
    await update.message.reply_text(
        f"ფოტო მიღებულია!\nPart Number: {part}\n\nდააჭირე დამუშავებას:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def process_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("AI მუშაობს ინფორმაციის მოძიებაზე...")
    await process_listing(update.callback_query.from_user.id, update.callback_query.message)

async def process_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("AI მუშაობს ინფორმაციის მოძიებაზე...")
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
        draft_res = await create_ebay_inventory_draft(listing)
        user_sessions[user_id] = {"part_number": None, "photos": []}
        compat_list = listing.get("compatibility", [])[:5]
        compat_text = "\n".join("- " + c for c in compat_list)
        
        result = (
            f"დრაფტის პროცესი დასრულდა!\n\nსათაური: {listing.get('title', '')}\n"
            f"ფასი: ${listing.get('suggested_price', 'N/A')}\nბრენდი: {listing.get('brand', 'Unbranded')}\n"
            f"მდგომარეობა: {listing.get('condition', 'Used')}\n\nთავსებადობა:\n{compat_text}\n\n"
            f"სტატუსი: {draft_res}\n\n"
            "შეხედე შენს eBay Seller Hub -> Drafts გვერდს!"
        )
        await message.reply_text(result)
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
        "CRITICAL: For 'brand', provide the actual car brand (e.g. Toyota, Honda, Ford, BMW, Mopar). NEVER use 'OEM'. If unknown, use 'Unbranded'.\n\n"
        "Respond ONLY with valid JSON, no markdown:\n"
        "{\n"
        '  "title": "eBay title max 80 chars SEO optimized with part number",\n'
        '  "description": "Detailed description plain text",\n'
        '  "category_id": "262",\n'
        '  "condition": "Used",\n'
        '  "suggested_price": 25,\n'
        '  "compatibility": ["Year Make Model"],\n'
        '  "oem_number": "OEM part number",\n'
        '  "brand": "Toyota",\n'
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

async def create_ebay_inventory_draft(listing):
    if not EBAY_OAUTH_TOKEN: return "ERROR: NO_EBAY_OAUTH_TOKEN_IN_VARIABLES"
    
    # ვიყენებთ SKU-სთვის დინამიურ უნიკალურ კოდს
    sku = f"AUTO-{listing.get('part_number', 'PART')}-{int(os.getpid())}"
    url = f"https://api.ebay.com/sell/inventory/v1/inventory_item/{sku}"
    
    headers = {
        "Authorization": f"Bearer {EBAY_OAUTH_TOKEN}",
        "Content-Type": "application/json",
        "Content-Language": "en-US"
    }
    
    brand = "Unbranded" if listing.get("brand", "Unbranded").upper() == "OEM" else listing.get("brand", "Unbranded")
    part_num = listing.get("part_number", listing.get("oem_number", "Universal"))

    payload = {
        "availability": {"shipToLocationAvailability": {"quantity": 1}},
        "condition": "USED",
        "product": {
            "title": listing.get("title", "Auto Part")[:80],
            "description": listing.get("description", "No description"),
            "aspects": {
                "Brand": [brand],
                "Manufacturer Part Number": [part_num],
                "Type": ["Direct Replacement"]
            },
            "imageUrls": ["https://upload.wikimedia.org/wikipedia/commons/thumb/e/e0/Car_with_Driver-Side_A-Pillar_Highlighted.jpg/800px-Car_with_Driver-Side_A-Pillar_Highlighted.jpg"]
        }
    }
    
    try:
        response = requests.put(url, headers=headers, json=payload, timeout=30)
        if response.status_code in [200, 201, 204]:
            return f"✅ წარმატებით ჩაიწერა (SKU: {sku})"
        return f"eBay REST API შეტყობინება: {response.text[:200]}"
    except Exception as e:
        logger.error("eBay Inventory Error: " + str(e))
        return "LOCAL_DRAFT"

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
