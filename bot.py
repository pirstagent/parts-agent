import os
import logging
import base64
import json
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CLAUDE_API_KEY = os.environ.get("CLAUDE_API_KEY")
EBAY_APP_ID = os.environ.get("EBAY_APP_ID")
EBAY_USER_TOKEN = os.environ.get("EBAY_USER_TOKEN")

user_sessions = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 გამარჯობა! მე ვარ eBay ავტონაწილების აგენტი.\n\n"
        "📦 გამომიგზავნე:\n"
        "• Part Number (მაგ: BMW-11127788047)\n"
        "• ან ფოტო ნაწილისა\n"
        "• ან ორივე ერთად\n\n"
        "მე ვიპოვი ინფოს და eBay Draft-ში შევინახავ! 🚀"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text or ""
    
    if user_id not in user_sessions:
        user_sessions[user_id] = {"part_number": None, "photos": []}
    
    if text:
        user_sessions[user_id]["part_number"] = text.strip()
    
    await update.message.reply_text("🔍 Part number მიღებულია! თუ ფოტოც გაქვს გამომიგზავნე, ან დაწერე /process დასამუშავებლად.")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id not in user_sessions:
        user_sessions[user_id] = {"part_number": None, "photos": []}
    
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    
    photo_bytes = await file.download_as_bytearray()
    photo_b64 = base64.b64encode(photo_bytes).decode('utf-8')
    user_sessions[user_id]["photos"].append(photo_b64)
    
    caption = update.message.caption
    if caption:
        user_sessions[user_id]["part_number"] = caption.strip()
    
    photo_count = len(user_sessions[user_id]["photos"])
    part = user_sessions[user_id]["part_number"] or "არ არის"
    
    keyboard = [[InlineKeyboardButton("▶ დამუშავება", callback_data="process")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"📸 ფოტო {photo_count} მიღებულია!\n"
        f"🔢 Part Number: {part}\n\n"
        f"კიდევ ფოტო გინდა დამატება? ან დააჭირე დამუშავებას:",
        reply_markup=reply_markup
    )

async def process_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    await query.message.reply_text("⏳ AI მუშაობს...")
    await process_listing(user_id, query.message)

async def process_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.message.reply_text("⏳ AI მუშაობს...")
    await process_listing(user_id, update.message)

async def process_listing(user_id, message):
    session = user_sessions.get(user_id, {})
    part_number = session.get("part_number", "")
    photos = session.get("photos", [])
    
    if not part_number and not photos:
        await message.reply_text("⚠️ გამომიგზავნე Part Number ან ფოტო პირველ რიგში!")
        return
    
    try:
        listing = await call_claude(part_number, photos)
        draft_id = await create_ebay_draft(listing)
        
        user_sessions[user_id] = {"part_number": None, "photos": []}
        
        compat_text = "\n".join(f"  • {c}" for c in listing.get("compatibility", [])[:5])
        
        await message.reply_text(
            f"✅ განცხადება მზადაა!\n\n"
            f"📦 *{listing.get('title', '')}*\n"
            f"💰 ფასი: ${listing.get('suggested_price', 'N/A')}\n"
            f"🏷 მდგომარეობა: {listing.get('condition', 'Used')}\n\n"
            f"🚗 თავსებადობა:\n{compat_text}\n\n"
            f"📋 Draft ID: `{draft_id}`\n"
            f"🔗 eBay Seller Hub-ში გააქტიურე!",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Error: {e}")
        await message.reply_text(f"❌ შეცდომა: {str(e)}\n\nსცადე თავიდან /start")

async def call_claude(part_number: str, photos: list) -> dict:
    headers = {
        "x-api-key": CLAUDE_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json"
    }
    
    content = []
    
    for photo_b64 in photos[:4]:
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": photo_b64}
        })
    
    prompt = f"""You are an expert eBay auto parts specialist. Analyze this auto part.
Part Number: {part_number or 'See image'}

Search your knowledge for this part number and provide complete eBay listing details.

Respond ONLY with valid JSON (no markdown):
{{
  "title": "eBay title max 80 chars, SEO optimized with part number",
  "description": "Detailed HTML description 3-4 paragraphs",
  "category_id": "eBay Motors category ID as string",
  "condition": "Used or New",
  "suggested_price": "recommended price in USD as number",
  "compatibility": ["Year Make Model", "Year Make Model"],
  "oem_number": "OEM part number if known",
  "brand": "brand name",
  "condition_description": "brief condition notes"
}}"""
    
    content.append({"type": "text", "text": prompt})
    
    payload = {
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 1500,
        "tools": [{"type": "web_search_20250305", "name": "web_search"}],
        "messages": [{"role": "user", "content": content}]
    }
    
    response = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers=headers,
        json=payload,
        timeout=60
    )
    
    data = response.json()
    
    text = ""
    for block in data.get("content", []):
        if block.get("type") == "text":
            text += block.get("text", "")
    
    text = text.replace("```json", "").replace("```", "").strip()
    return json.loads(text)

async def create_ebay_draft(listing: dict) -> str:
    if not EBAY_USER_TOKEN:
        return "NO_TOKEN_CONFIGURED"
    
    headers = {
        "Authorization": f"IAF {EBAY_USER_TOKEN}",
        "Content-Type": "application/json",
        "X-EBAY-API-SITEID": "0",
        "X-EBAY-API-COMPATIBILITY-LEVEL": "967",
        "X-EBAY-API-CALL-NAME": "AddItem",
        "X-EBAY-API-APP-NAME": EBAY_APP_ID or "",
    }
    
    xml_body = f"""<?xml version="1.0" encoding="utf-8"?>
<AddItemRequest xmlns="urn:ebay:apis:eBLBaseComponents">
  <RequesterCredentials>
    <eBayAuthToken>{EBAY_USER_TOKEN}</eBayAuthToken>
  </RequesterCredentials>
  <Item>
    <Title>{listing.get('title', 'Auto Part')[:80]}</Title>
    <Description><![CDATA[{listing.get('description', '')}]]></Description>
    <PrimaryCategory>
      <CategoryID>{listing.get('category_id', '6030')}</CategoryID>
    </PrimaryCategory>
    <StartPrice>{listing.get('suggested_price', 25)}</StartPrice>
    <Condition>{listing.get('condition', 'Used')}</Condition>
    <Country>US</Country>
    <Currency>USD</Currency>
    <DispatchTimeMax>3</DispatchTimeMax>
    <ListingDuration>GTC</ListingDuration>
    <ListingType>FixedPriceItem</ListingType>
    <Quantity>1</Quantity>
    <ShipToLocations>US</ShipToLocations>
  </Item>
</AddItemRequest>"""
    
    try:
        response = requests.post(
            "https://api.ebay.com/ws/api.dll",
            headers=headers,
            data=xml_body.encode('utf-8'),
            timeout=30
        )
        if "ItemID" in response.text:
            import re
            match = re.search(r'<ItemID>(\d+)</ItemID>', response.text)
            if match:
                return match.group(1)
        return "DRAFT_SAVED"
    except Exception as e:
        logger.error(f"eBay API error: {e}")
        return "DRAFT_LOCAL"

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
