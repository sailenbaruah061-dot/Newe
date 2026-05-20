import logging
import asyncio
import psutil
import time
import os
from datetime import datetime
from typing import Dict
from threading import Thread

from flask import Flask, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from telegram.constants import ParseMode
from pymongo import MongoClient
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# ============ CONFIGURATION ============
TOKEN = os.getenv("BOT_TOKEN")
MONGO_URL = os.getenv("MONGO_URL")
OWNER_ID = int(os.getenv("OWNER_ID", "8722144519"))
OWNER_USERNAME = os.getenv("OWNER_USERNAME", "")
GROUP_LINK = os.getenv("GROUP_LINK", "https://t.me/+Yu4K5-9LHH1mM2Zl")
SUDO_GROUP_LINK = os.getenv("SUDO_GROUP_LINK", "https://t.me/+zzukV0c4p5swOWRh")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# Validation
if not TOKEN:
    raise ValueError("BOT_TOKEN environment variable is required!")
if not MONGO_URL:
    raise ValueError("MONGO_URL environment variable is required!")

# ============ GEMINI AI SETUP ============
try:
    import google.generativeai as genai
    AI_AVAILABLE = False
    ai_model = None
    
    if GEMINI_API_KEY:
        genai.configure(api_key=GEMINI_API_KEY)
        ai_model = genai.GenerativeModel('gemini-1.5-flash')
        AI_AVAILABLE = True
        print("✅ Gemini AI is ready!")
    else:
        print("⚠️ GEMINI_API_KEY not found! AI features disabled.")
except ImportError:
    AI_AVAILABLE = False
    print("⚠️ google-generativeai not installed! Run: pip install google-generativeai")
except Exception as e:
    AI_AVAILABLE = False
    print(f"⚠️ AI Setup Error: {e}")

# ============ FLASK APP FOR RENDER ============
flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return jsonify({
        "status": "alive",
        "bot": "running",
        "ai": AI_AVAILABLE,
        "message": "✅ Bot is active on Render!"
    })

@flask_app.route('/health')
def health():
    return jsonify({"status": "healthy"})

def run_flask():
    port = int(os.environ.get('PORT', 8080))
    flask_app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

# ============ DATABASE SETUP ============
client = MongoClient(MONGO_URL)
db = client["telegram_bot"]
sudo_users_db = db["sudo_users"]
muted_users_db = db["muted_users"]
filters_db = db["filters"]
stickers_db = db["stickers"]
welcome_db = db["welcome"]
owner_settings_db = db["owner_settings"]
ai_chat_db = db["ai_chat"]  # New collection for AI settings

# ============ LOGGING ============
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', 
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ============ GLOBAL VARIABLES ============
active_spams: Dict[int, asyncio.Task] = {}

# ============ AI HELPER FUNCTIONS ============
async def get_ai_reply(message: str, user_name: str, chat_id: int) -> str:
    """Get reply from Gemini AI"""
    if not AI_AVAILABLE or not ai_model:
        return "❌ AI is not configured! Please contact bot owner."
    
    # Check if AI is enabled in this chat
    ai_setting = ai_chat_db.find_one({"chat_id": chat_id})
    if ai_setting and ai_setting.get("enabled") == False:
        return None  # AI disabled in this chat
    
    try:
        # Get chat history or personality
        personality = "You are a friendly, helpful Telegram bot assistant named 'Dark Bot'. Reply in a friendly way. Keep responses short and useful (1-2 lines maximum). Use emojis occasionally."
        
        if ai_setting and ai_setting.get("custom_prompt"):
            personality = ai_setting.get("custom_prompt")
        
        prompt = f"""{personality}

User Name: {user_name}
User Message: {message}

Reply:"""
        
        response = ai_model.generate_content(prompt)
        return response.text[:500]  # Limit to 500 characters
        
    except Exception as e:
        logger.error(f"AI Error: {e}")
        return f"❌ AI Error: {str(e)[:100]}"

# ============ USER COMMANDS ============
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    owner_link, owner_uname = await get_owner_link()
    keyboard = [
        [InlineKeyboardButton("➕ Add Me Baby", url=f"https://t.me/{context.bot.username}?startgroup=true")],
        [InlineKeyboardButton("🏠 My Home", url=GROUP_LINK),
         InlineKeyboardButton("👑 My Master", url=owner_link)],
        [InlineKeyboardButton("❓ Help", callback_data="help"),
         InlineKeyboardButton("⚡ Get Sudo", url=SUDO_GROUP_LINK)],
        [InlineKeyboardButton("🤖 Chat AI", callback_data="ai_info")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    caption = "✨ **Bot Started Successfully!** ✨\n\nI'm here to help you manage your groups!"
    if AI_AVAILABLE:
        caption += "\n\n🤖 **AI Feature:** Use /ai <question> or mention me!"
    if owner_uname:
        caption += f"\n\n👑 **My Master:** @{owner_uname}"
    
    await update.message.reply_text(
        text=caption,
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ai_section = ""
    if AI_AVAILABLE:
        ai_section = """
🤖 **AI Commands:**
• /ai <question> - Ask AI anything
• /ai_enable - Enable AI in group
• /ai_disable - Disable AI in group
• /ai_prompt <prompt> - Set custom AI personality
• Mention @bot - Chat with AI by mentioning bot

"""
    
    help_text = f"""
🤖 **Bot Commands:**

📌 **User Commands:**
• /ping - Check bot speed
• /alive - Check bot status  
• /speed - Bot performance
{ai_section}
👑 **Admin Commands:**
• /ban @user - Ban a user
• /mute @user - Mute a user
• /unmute @user - Unmute a user
• /promote @user - Promote to admin
• /filter keyword reply - Save auto-reply
• /welcome message - Set welcome message
• /mention - Mention all members

⚡ **Sudo Commands:**
• .mute - Mute all messages in group
• .unmute - Unmute all messages
• .sticker count - Send multiple stickers
• .spam @user count message - Spam user
• .stopspam - Stop spamming
• .info @user - Get user details

🔧 **Owner Only:**
• .addsudo @user - Add sudo user
• .delsudo @user - Remove sudo user
• .sudolist - List all sudo users
• .mutelist - List all muted users
• .addsticker - Add sticker (reply to sticker)
• /setowner @username - Set owner username

💡 **Tip:** Reply to a user's message to ban/mute/promote them!
"""
    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)

async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    start_time = time.time()
    message = await update.message.reply_text("🏓 Pinging...")
    end_time = time.time()
    ping_time = round((end_time - start_time) * 1000)
    await message.delete()
    speed = await get_bot_speed()
    
    await update.message.reply_text(
        text=f"🏓 **Pong!**\n\n"
             f"⚡ **Response Time:** `{ping_time}ms`\n"
             f"💻 **CPU Usage:** `{speed['cpu']}%`\n"
             f"📊 **Memory Usage:** `{speed['memory']}%`\n"
             f"🌐 **Network:** `{speed['ping']}MB`\n"
             f"🤖 **AI Status:** `{'✅ Active' if AI_AVAILABLE else '❌ Disabled'}`",
        parse_mode=ParseMode.MARKDOWN
    )

async def alive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    speed = await get_bot_speed()
    await update.message.reply_text(
        text=f"✅ **Bot is Alive!**\n\n"
             f"🕒 **Status:** Running 24/7\n"
             f"💻 **CPU:** `{speed['cpu']}%`\n"
             f"📊 **Memory:** `{speed['memory']}%`\n"
             f"🤖 **AI:** `{'Active' if AI_AVAILABLE else 'Inactive'}`\n"
             f"🎯 **Ready to work!**",
        parse_mode=ParseMode.MARKDOWN
    )

async def speed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await ping(update, context)

# ============ AI COMMANDS ============
async def ai_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /ai command"""
    if not AI_AVAILABLE:
        await update.message.reply_text("❌ AI feature is not configured! Please contact bot owner.")
        return
    
    if not context.args:
        await update.message.reply_text(
            "🤖 **How to use AI:**\n\n"
            "• `/ai <question>` - Ask anything\n"
            "• `@bot <question>` - Mention me with question\n"
            "• `/ai_enable` - Enable AI in group\n"
            "• `/ai_disable` - Disable AI in group\n\n"
            "**Example:** `/ai What is Python?`",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    question = ' '.join(context.args)
    user_name = update.effective_user.first_name
    chat_id = update.effective_chat.id
    
    # Send typing indicator
    await update.message.chat.send_action(action="typing")
    await asyncio.sleep(0.5)
    
    reply = await get_ai_reply(question, user_name, chat_id)
    
    if reply:
        await update.message.reply_text(f"🤖 **AI:** {reply}", parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text("❌ AI is disabled in this group! Ask admin to enable with /ai_enable")

async def ai_enable(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Enable AI in group (admin only)"""
    if not AI_AVAILABLE:
        await update.message.reply_text("❌ AI feature is not configured!")
        return
    
    if not update.effective_chat.type in ['group', 'supergroup']:
        await update.message.reply_text("❌ This command only works in groups!")
        return
    
    user_id = update.effective_user.id
    member = await update.effective_chat.get_member(user_id)
    
    if not member.can_restrict_members and not await is_sudo(user_id):
        await update.message.reply_text("❌ You need admin rights to enable AI!")
        return
    
    ai_chat_db.update_one(
        {"chat_id": update.effective_chat.id},
        {"$set": {"enabled": True, "enabled_by": user_id, "enabled_at": datetime.now()}},
        upsert=True
    )
    await update.message.reply_text("✅ **AI is now ENABLED in this group!**\n\nUsers can now chat with AI using /ai command or by mentioning me.", parse_mode=ParseMode.MARKDOWN)

async def ai_disable(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Disable AI in group (admin only)"""
    if not AI_AVAILABLE:
        await update.message.reply_text("❌ AI feature is not configured!")
        return
    
    if not update.effective_chat.type in ['group', 'supergroup']:
        await update.message.reply_text("❌ This command only works in groups!")
        return
    
    user_id = update.effective_user.id
    member = await update.effective_chat.get_member(user_id)
    
    if not member.can_restrict_members and not await is_sudo(user_id):
        await update.message.reply_text("❌ You need admin rights to disable AI!")
        return
    
    ai_chat_db.update_one(
        {"chat_id": update.effective_chat.id},
        {"$set": {"enabled": False, "disabled_by": user_id, "disabled_at": datetime.now()}},
        upsert=True
    )
    await update.message.reply_text("❌ **AI is now DISABLED in this group!**", parse_mode=ParseMode.MARKDOWN)

async def ai_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set custom AI personality (admin only)"""
    if not AI_AVAILABLE:
        await update.message.reply_text("❌ AI feature is not configured!")
        return
    
    if not update.effective_chat.type in ['group', 'supergroup']:
        await update.message.reply_text("❌ This command only works in groups!")
        return
    
    user_id = update.effective_user.id
    member = await update.effective_chat.get_member(user_id)
    
    if not member.can_restrict_members and not await is_sudo(user_id):
        await update.message.reply_text("❌ You need admin rights to set AI prompt!")
        return
    
    if not context.args:
        await update.message.reply_text(
            "🤖 **Set AI Personality:**\n\n"
            "Usage: `/ai_prompt <prompt>`\n\n"
            "**Example:**\n"
            "`/ai_prompt You are a funny assistant who replies with jokes`\n\n"
            "`/ai_prompt reset` - Reset to default",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    prompt = ' '.join(context.args)
    
    if prompt.lower() == 'reset':
        ai_chat_db.update_one(
            {"chat_id": update.effective_chat.id},
            {"$unset": {"custom_prompt": ""}}
        )
        await update.message.reply_text("✅ AI personality reset to default!")
    else:
        ai_chat_db.update_one(
            {"chat_id": update.effective_chat.id},
            {"$set": {"custom_prompt": prompt, "prompt_set_by": user_id, "prompt_set_at": datetime.now()}},
            upsert=True
        )
        await update.message.reply_text(f"✅ AI personality set!\n\n**New prompt:** `{prompt[:100]}`", parse_mode=ParseMode.MARKDOWN)

async def ai_mention_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reply when bot is mentioned"""
    if not AI_AVAILABLE:
        return
    
    if not update.message or not update.message.text:
        return
    
    bot_username = context.bot.username
    text = update.message.text
    
    # Check if bot is mentioned
    if f"@{bot_username}" in text or bot_username in text:
        # Remove bot mention from text
        question = text.replace(f"@{bot_username}", "").replace(bot_username, "").strip()
        
        if not question:
            await update.message.reply_text(
                f"🤖 Hello! I'm {context.bot.first_name}\n\n"
                f"Ask me something like:\n"
                f"`@{bot_username} What is Python?`\n\n"
                f"Or use /ai command!",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        user_name = update.effective_user.first_name
        chat_id = update.effective_chat.id
        
        # Send typing indicator
        await update.message.chat.send_action(action="typing")
        await asyncio.sleep(0.5)
        
        reply = await get_ai_reply(question, user_name, chat_id)
        
        if reply:
            await update.message.reply_text(f"🤖 **AI:** {reply}", parse_mode=ParseMode.MARKDOWN)

async def set_owner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("❌ Only bot owner can use this command!")
        return
    
    if not context.args:
        await update.message.reply_text("Usage: /setowner @username\nExample: /setowner @myusername")
        return
    
    username = context.args[0].replace('@', '')
    
    owner_settings_db.update_one(
        {"_id": "owner_config"},
        {"$set": {"username": username, "updated_at": datetime.now()}},
        upsert=True
    )
    
    await update.message.reply_text(f"✅ Owner username set to: @{username}\n\nNow '👑 My Master' button will open @{username}")

# ============ ADMIN COMMANDS ============
async def ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat.type in ['group', 'supergroup']:
        await update.message.reply_text("❌ This command only works in groups!")
        return
    
    user_id = update.effective_user.id
    member = await update.effective_chat.get_member(user_id)
    
    if not member.can_restrict_members:
        await update.message.reply_text("❌ You don't have permission to ban!")
        return
    
    try:
        if update.message.reply_to_message:
            user_to_ban = update.message.reply_to_message.from_user.id
            name = update.message.reply_to_message.from_user.first_name
        elif context.args:
            username = context.args[0].replace('@', '')
            try:
                user = await context.bot.get_chat(username)
                user_to_ban = user.id
                name = user.first_name
            except:
                await update.message.reply_text("❌ User not found!")
                return
        else:
            await update.message.reply_text("Usage: /ban @username or reply to user")
            return
        
        await update.effective_chat.ban_member(user_to_ban)
        await update.message.reply_text(f"✅ {name} has been banned!")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")

async def mute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat.type in ['group', 'supergroup']:
        await update.message.reply_text("❌ This command only works in groups!")
        return
    
    user_id = update.effective_user.id
    member = await update.effective_chat.get_member(user_id)
    
    if not member.can_restrict_members:
        await update.message.reply_text("❌ You don't have permission to mute!")
        return
    
    if update.message.reply_to_message:
        user_to_mute = update.message.reply_to_message.from_user.id
        name = update.message.reply_to_message.from_user.first_name
        await update.effective_chat.restrict_member(
            user_to_mute,
            permissions=ChatPermissions(can_send_messages=False)
        )
        await update.message.reply_text(f"✅ {name} has been muted!")
    else:
        await update.message.reply_text("❌ Reply to a user to mute them!")

async def unmute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat.type in ['group', 'supergroup']:
        await update.message.reply_text("❌ This command only works in groups!")
        return
    
    if update.message.reply_to_message:
        user_to_unmute = update.message.reply_to_message.from_user.id
        name = update.message.reply_to_message.from_user.first_name
        await update.effective_chat.restrict_member(
            user_to_unmute,
            permissions=ChatPermissions(
                can_send_messages=True,
                can_send_media_messages=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True
            )
        )
        await update.message.reply_text(f"✅ {name} has been unmuted!")
    else:
        await update.message.reply_text("❌ Reply to a user to unmute them!")

async def save_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /filter keyword reply_text\nExample: /filter hi Hello! How are you?")
        return
    
    keyword = context.args[0].lower()
    reply = ' '.join(context.args[1:])
    
    filters_db.update_one(
        {"chat_id": update.effective_chat.id, "keyword": keyword},
        {"$set": {"reply": reply}},
        upsert=True
    )
    await update.message.reply_text(f"✅ Filter saved for '{keyword}'")

async def handle_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    
    text = update.message.text.lower()
    filter_data = filters_db.find_one({"chat_id": update.effective_chat.id, "keyword": text})
    
    if filter_data:
        await update.message.reply_text(filter_data['reply'])

async def set_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /welcome Your welcome message\nUse {user} for member name")
        return
    
    welcome_msg = ' '.join(context.args)
    welcome_db.update_one(
        {"chat_id": update.effective_chat.id},
        {"$set": {"message": welcome_msg}},
        upsert=True
    )
    await update.message.reply_text("✅ Welcome message saved!")

async def welcome_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for member in update.message.new_chat_members:
        if member.is_bot:
            continue
        welcome_data = welcome_db.find_one({"chat_id": update.effective_chat.id})
        if welcome_data:
            msg = welcome_data['message'].format(user=member.first_name)
            await update.message.reply_text(msg)

async def promote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat.type in ['group', 'supergroup']:
        await update.message.reply_text("❌ This command only works in groups!")
        return
    
    user_id = update.effective_user.id
    member = await update.effective_chat.get_member(user_id)
    
    if not member.can_promote_members:
        await update.message.reply_text("❌ You don't have permission to promote!")
        return
    
    if update.message.reply_to_message:
        user_to_promote = update.message.reply_to_message.from_user.id
        name = update.message.reply_to_message.from_user.first_name
        await update.effective_chat.promote_member(
            user_to_promote,
            can_change_info=True,
            can_post_messages=True,
            can_edit_messages=True,
            can_delete_messages=True,
            can_invite_users=True,
            can_restrict_members=True,
            can_pin_messages=True,
            can_promote_members=False
        )
        await update.message.reply_text(f"✅ {name} is now an admin!")
    else:
        await update.message.reply_text("❌ Reply to a user to promote them!")

async def mention_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat.type in ['group', 'supergroup']:
        await update.message.reply_text("❌ This command only works in groups!")
        return
    
    user_id = update.effective_user.id
    member = await update.effective_chat.get_member(user_id)
    
    if not member.can_mention_all and not await is_sudo(user_id):
        await update.message.reply_text("❌ You need admin rights to mention all!")
        return
    
    admins = []
    async for admin in update.effective_chat.get_administrators():
        if not admin.user.is_bot:
            if admin.user.username:
                admins.append(f"@{admin.user.username}")
            else:
                admins.append(admin.user.first_name)
    
    if admins:
        mentions = " ".join(admins[:15])
        await update.message.reply_text(f"📢 **Admins in this group:**\n{mentions}", parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text("No admins found!")

# ============ SUDO COMMANDS ============
async def sudo_mute_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_sudo(update.effective_user.id):
        await update.message.reply_text("❌ Only sudo users can use this command!")
        return
    
    if not update.effective_chat.type in ['group', 'supergroup']:
        await update.message.reply_text("❌ This command only works in groups!")
        return
    
    muted_users_db.update_one(
        {"chat_id": update.effective_chat.id},
        {"$set": {"muted": True, "muted_by": update.effective_user.id}},
        upsert=True
    )
    await update.message.reply_text("🔇 **All users are now muted in this group!**\nOnly admins and sudo users can send messages.", parse_mode=ParseMode.MARKDOWN)

async def sudo_unmute_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_sudo(update.effective_user.id):
        await update.message.reply_text("❌ Only sudo users can use this command!")
        return
    
    if not update.effective_chat.type in ['group', 'supergroup']:
        await update.message.reply_text("❌ This command only works in groups!")
        return
    
    muted_users_db.delete_many({"chat_id": update.effective_chat.id})
    await update.message.reply_text("🔊 **All users can now send messages in this group!**", parse_mode=ParseMode.MARKDOWN)

async def sudo_sticker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_sudo(update.effective_user.id):
        await update.message.reply_text("❌ Only sudo users can use this command!")
        return
    
    if not context.args:
        await update.message.reply_text("Usage: .sticker <count>\nExample: .sticker 5")
        return
    
    try:
        count = int(context.args[0])
        if count > 20:
            count = 20
        if count < 1:
            count = 1
        
        stickers = list(stickers_db.find())
        if not stickers:
            await update.message.reply_text("❌ No stickers saved! Use .addsticker to add stickers.")
            return
        
        for i in range(min(count, len(stickers))):
            await update.message.reply_sticker(stickers[i]['sticker_id'])
            await asyncio.sleep(0.5)
    except ValueError:
        await update.message.reply_text("❌ Please provide a valid number!")

async def sudo_spam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_sudo(update.effective_user.id):
        await update.message.reply_text("❌ Only sudo users can use this command!")
        return
    
    if len(context.args) < 3:
        await update.message.reply_text("Usage: .spam @user <count> <message>\nExample: .spam @username 10 Hello!")
        return
    
    user_input = context.args[0].replace('@', '')
    try:
        count = int(context.args[1])
        message = ' '.join(context.args[2:])
        
        if count > 50:
            count = 50
        if count < 1:
            count = 1
        
        try:
            user = await context.bot.get_chat(user_input)
        except:
            await update.message.reply_text("❌ User not found!")
            return
        
        async def spam_task():
            for i in range(count):
                await update.message.reply_text(f"@{user.username if user.username else user_input} {message} [{i+1}]")
                await asyncio.sleep(1)
        
        task = asyncio.create_task(spam_task())
        active_spams[update.effective_chat.id] = task
        await update.message.reply_text(f"✅ Spamming @{user.username if user.username else user_input} for {count} messages!")
    except ValueError:
        await update.message.reply_text("❌ Please provide a valid count!")

async def sudo_stopspam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_sudo(update.effective_user.id):
        await update.message.reply_text("❌ Only sudo users can use this command!")
        return
    
    if update.effective_chat.id in active_spams:
        active_spams[update.effective_chat.id].cancel()
        del active_spams[update.effective_chat.id]
        await update.message.reply_text("✅ Spamming stopped!")
    else:
        await update.message.reply_text("❌ No active spam in this chat!")

async def sudo_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_sudo(update.effective_user.id):
        await update.message.reply_text("❌ Only sudo users can use this command!")
        return
    
    target_user = None
    if update.message.reply_to_message:
        target_user = update.message.reply_to_message.from_user
    elif context.args:
        username = context.args[0].replace('@', '')
        try:
            target_user = await context.bot.get_chat(username)
        except:
            await update.message.reply_text("❌ User not found!")
            return
    
    if not target_user:
        await update.message.reply_text("❌ Reply to a user or provide username!")
        return
    
    info_text = f"""
📊 **User Information:**
• **Name:** {target_user.first_name}
• **ID:** `{target_user.id}`
• **Username:** @{target_user.username if target_user.username else 'None'}
• **Is Bot:** {target_user.is_bot}
• **Is Sudo:** {await is_sudo(target_user.id)}
    """
    await update.message.reply_text(info_text, parse_mode=ParseMode.MARKDOWN)

# ============ OWNER COMMANDS ============
async def owner_addsudo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("❌ Only bot owner can use this command!")
        return
    
    if not context.args:
        await update.message.reply_text("Usage: .addsudo @username\nExample: .addsudo @username")
        return
    
    username = context.args[0].replace('@', '')
    try:
        user = await context.bot.get_chat(username)
        sudo_users_db.update_one(
            {"user_id": user.id},
            {"$set": {"username": username, "added_by": OWNER_ID, "added_at": datetime.now()}},
            upsert=True
        )
        await update.message.reply_text(f"✅ @{username} is now a sudo user!")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")

async def owner_delsudo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("❌ Only bot owner can use this command!")
        return
    
    if not context.args:
        await update.message.reply_text("Usage: .delsudo @username\nExample: .delsudo @username")
        return
    
    username = context.args[0].replace('@', '')
    result = sudo_users_db.delete_one({"username": username})
    
    if result.deleted_count > 0:
        await update.message.reply_text(f"✅ Removed @{username} from sudo users!")
    else:
        await update.message.reply_text(f"❌ @{username} is not a sudo user!")

async def owner_sudolist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("❌ Only bot owner can use this command!")
        return
    
    sudo_users = list(sudo_users_db.find())
    if not sudo_users:
        await update.message.reply_text("No sudo users found!")
        return
    
    text = "👑 **Sudo Users List:**\n\n"
    for i, user in enumerate(sudo_users, 1):
        text += f"{i}. @{user['username']} (ID: `{user['user_id']}`)\n"
    
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def owner_mutelist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("❌ Only bot owner can use this command!")
        return
    
    muted_chats = list(muted_users_db.find())
    if not muted_chats:
        await update.message.reply_text("No muted groups found!")
        return
    
    text = "🔇 **Muted Groups List:**\n\n"
    for i, chat in enumerate(muted_chats, 1):
        if chat.get("chat_id"):
            text += f"{i}. Chat ID: `{chat['chat_id']}`\n"
    
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def owner_addsticker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("❌ Only bot owner can use this command!")
        return
    
    if not update.message.reply_to_message or not update.message.reply_to_message.sticker:
        await update.message.reply_text("❌ Reply to a sticker to add it!\nExample: Reply to any sticker with .addsticker")
        return
    
    sticker = update.message.reply_to_message.sticker
    stickers_db.insert_one({
        "sticker_id": sticker.file_id,
        "added_by": OWNER_ID,
        "emoji": sticker.emoji,
        "added_at": datetime.now()
    })
    await update.message.reply_text("✅ Sticker added successfully!")

# ============ MESSAGE HANDLER ============
async def handle_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.from_user:
        return
    
    chat_id = update.effective_chat.id
    muted = muted_users_db.find_one({"chat_id": chat_id})
    
    if muted and muted.get("muted", False):
        user_id = update.effective_user.id
        is_user_admin = False
        
        try:
            member = await update.effective_chat.get_member(user_id)
            is_user_admin = member.status in ['administrator', 'creator']
        except:
            pass
        
        if not is_user_admin and not await is_sudo(user_id) and user_id != OWNER_ID:
            try:
                await update.message.delete()
            except:
                pass

# ============ HELPER FUNCTIONS ============
async def is_sudo(user_id: int) -> bool:
    if user_id == OWNER_ID:
        return True
    return sudo_users_db.find_one({"user_id": user_id}) is not None

async def get_bot_speed() -> dict:
    cpu_percent = psutil.cpu_percent()
    memory_percent = psutil.virtual_memory().percent
    ping = round(psutil.net_io_counters().bytes_sent / 1024 / 1024, 2)
    return {"cpu": cpu_percent, "memory": memory_percent, "ping": ping}

async def get_owner_link():
    owner_config = owner_settings_db.find_one({"_id": "owner_config"})
    if owner_config and owner_config.get("username"):
        username = owner_config.get("username").replace('@', '')
        return f"https://t.me/{username}", username
    if OWNER_USERNAME:
        username = OWNER_USERNAME.replace('@', '')
        return f"https://t.me/{username}", username
    return f"tg://user?id={OWNER_ID}", None

# ============ CALLBACK HANDLER ============
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "help":
        await help_command(update, context)
    elif query.data == "ai_info":
        ai_status = "✅ Active" if AI_AVAILABLE else "❌ Disabled"
        await query.edit_message_text(
            f"🤖 **AI Feature Information**\n\n"
            f"**Status:** {ai_status}\n"
            f"**Model:** Gemini 1.5 Flash\n"
            f"**Features:**\n"
            f"• Answer questions\n"
            f"• Chat conversations\n"
            f"• Custom personality\n\n"
            f"**Commands:**\n"
            f"• `/ai <question>` - Ask AI\n"
            f"• `/ai_enable` - Enable AI in group\n"
            f"• `/ai_disable` - Disable AI in group\n"
            f"• `/ai_prompt <prompt>` - Set custom personality\n"
            f"• `@{context.bot.username} <question>` - Mention to chat",
            parse_mode=ParseMode.MARKDOWN
        )

# ============ ERROR HANDLER ============
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update {update} caused error {context.error}")

# ============ MAIN FUNCTION ============
def main():
    # Start Flask server for Render
    Thread(target=run_flask, daemon=True).start()
    
    # Build application
    application = Application.builder().token(TOKEN).build()
    
    # Command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("ping", ping))
    application.add_handler(CommandHandler("alive", alive))
    application.add_handler(CommandHandler("speed", speed))
    application.add_handler(CommandHandler("ban", ban))
    application.add_handler(CommandHandler("mute", mute))
    application.add_handler(CommandHandler("unmute", unmute))
    application.add_handler(CommandHandler("filter", save_filter))
    application.add_handler(CommandHandler("welcome", set_welcome))
    application.add_handler(CommandHandler("promote", promote))
    application.add_handler(CommandHandler("mention", mention_all))
    application.add_handler(CommandHandler("setowner", set_owner))
    
    # AI Command handlers
    if AI_AVAILABLE:
        application.add_handler(CommandHandler("ai", ai_chat))
        application.add_handler(CommandHandler("ai_enable", ai_enable))
        application.add_handler(CommandHandler("ai_disable", ai_disable))
        application.add_handler(CommandHandler("ai_prompt", ai_prompt))
    
    # Sudo command handlers (dot prefix)
    application.add_handler(MessageHandler(filters.Regex(r'^\.mute$'), sudo_mute_all))
    application.add_handler(MessageHandler(filters.Regex(r'^\.unmute$'), sudo_unmute_all))
    application.add_handler(MessageHandler(filters.Regex(r'^\.sticker\s+\d+$'), sudo_sticker))
    application.add_handler(MessageHandler(filters.Regex(r'^\.spam\s+'), sudo_spam))
    application.add_handler(MessageHandler(filters.Regex(r'^\.stopspam$'), sudo_stopspam))
    application.add_handler(MessageHandler(filters.Regex(r'^\.info\s+'), sudo_info))
    
    # Owner command handlers (dot prefix)
    application.add_handler(MessageHandler(filters.Regex(r'^\.addsudo\s+'), owner_addsudo))
    application.add_handler(MessageHandler(filters.Regex(r'^\.delsudo\s+'), owner_delsudo))
    application.add_handler(MessageHandler(filters.Regex(r'^\.sudolist$'), owner_sudolist))
    application.add_handler(MessageHandler(filters.Regex(r'^\.mutelist$'), owner_mutelist))
    application.add_handler(MessageHandler(filters.Regex(r'^\.addsticker$'), owner_addsticker))
    
    # Message handlers
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_filter))
    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome_new_member))
    application.add_handler(MessageHandler(filters.ALL, handle_messages))
    
    # AI mention handler (must be after other handlers)
    if AI_AVAILABLE:
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ai_mention_reply))
    
    # Callback and error handlers
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_error_handler(error_handler)
    
    # Start bot
    print("=" * 50)
    print("🤖 Bot is starting...")
    print(f"✅ Bot username: @{application.bot.username}")
    print(f"👑 Owner ID: {OWNER_ID}")
    if OWNER_USERNAME:
        print(f"👑 Owner Username: @{OWNER_USERNAME}")
    print(f"🤖 AI Status: {'✅ Active' if AI_AVAILABLE else '❌ Disabled'}")
    print("=" * 50)
    
    # Run bot with polling (works on Render)
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
