import asyncio
import os
import re
import aiohttp
import json
import random
from motor.motor_asyncio import AsyncIOMotorClient
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import (PhoneNumberInvalid, PhoneCodeInvalid, PhoneCodeExpired, SessionPasswordNeeded)
from telethon.sync import TelegramClient as TClient
from telethon.sessions import StringSession
from telethon.tl.functions.account import ReportPeerRequest
from telethon.tl.functions.messages import ReportRequest
from telethon.tl.types import (
    InputReportReasonChildAbuse, InputReportReasonViolence,
    InputReportReasonIllegalDrugs, InputReportReasonPersonalDetails,
    InputReportReasonSpam, InputReportReasonOther
)

# Configuration
API_ID = 23572045
API_HASH = "6bf81dff6563e3f1fb3c7d23a6872291"
BOT_TOKEN = "7884183496:AAF7VlXfwEY6lD1TvSFlA46vlxVTy4I_XTg"
OWNER_ID = 7577853954
MONGODB_URL = "mongodb+srv://tanjiro1564:tanjiro1564@cluster0.pp5yz4e.mongodb.net/?retryWrites=true&w=majority"
START_IMAGE_URL = "https://te.legra.ph/file/acc3bbc9896f9daee3915-952021b9936dc43a13.jpg"

# MongoDB setup
mongo_client = AsyncIOMotorClient(MONGODB_URL)
db = mongo_client.kirito_bot
users_collection = db.users
sessions_collection = db.sessions

# Data Storage
accounts = []
proxy_list = []
login_states = {}  # Track login states: {user_id: {"stage": "string_session", "client": client}}
waiting_for_link = {}  # Track users waiting to send message links
waiting_for_comment = {}  # Track users waiting to send comments
waiting_for_session = {}  # Track users waiting to send string session

# Load approved users from MongoDB
approved_users = set()

async def load_data_from_db():
    """Load approved users and sessions from MongoDB"""
    global approved_users, accounts

    # Load approved users
    approved_users_cursor = users_collection.find({"approved": True})
    async for user in approved_users_cursor:
        approved_users.add(user["user_id"])

    # Add owner if not present
    approved_users.add(OWNER_ID)

    # Load sessions
    sessions_cursor = sessions_collection.find({})
    async for session_data in sessions_cursor:
        try:
            client = TClient(StringSession(session_data["session_string"]), API_ID, API_HASH)
            await client.connect()
            accounts.append({
                "client": client,
                "user_id": session_data["user_id"],
                "session_id": session_data["_id"]
            })
        except Exception as e:
            print(f"Error loading session: {e}")

async def save_approved_user(user_id):
    """Save approved user to MongoDB"""
    await users_collection.update_one(
        {"user_id": user_id},
        {"$set": {"user_id": user_id, "approved": True}},
        upsert=True
    )

async def remove_approved_user(user_id):
    """Remove approved user from MongoDB"""
    await users_collection.update_one(
        {"user_id": user_id},
        {"$set": {"approved": False}}
    )

async def save_session_to_db(user_id, session_string):
    """Save session string to MongoDB"""
    await sessions_collection.insert_one({
        "user_id": user_id,
        "session_string": session_string
    })

app = Client("kirito-bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

async def fetch_photo(url):
    """Fetch photo from URL"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    return await response.read()
    except Exception as e:
        print(f"Error fetching photo: {e}")
    return None

def get_report_reason(reason):
    return {
        "child_abuse": InputReportReasonChildAbuse(),
        "violence": InputReportReasonViolence(),
        "drugs": InputReportReasonIllegalDrugs(),
        "weapons": InputReportReasonOther(),
        "phone": InputReportReasonPersonalDetails(),
        "images": InputReportReasonPersonalDetails(),
        "address": InputReportReasonPersonalDetails(),
        "spam": InputReportReasonSpam()
    }.get(reason, InputReportReasonOther())

@app.on_message(filters.command("start") & filters.private)
async def start(_, m: Message):
    if not m.from_user:
        return
    if m.from_user.id not in approved_users:
        return await m.reply("🚫 You're not approved to use this bot. Purchase Bot access to use it contact To @Whosekirito .")

    photo = await fetch_photo(START_IMAGE_URL)
    caption = (
        "👋 Welcome to **Kirito Report Bot**\n\n"
        "With this bot you can:\n"
        "➤ Report messages, groups, or channels\n"
        "➤ Use multiple Telegram accounts\n"
        "➤ Add comments and select reason for report\n\n"
        "Type /help to see all available commands."
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Uᴘᴅᴀᴛᴇs", url="https://t.me/Kirito_Bots")],
        [InlineKeyboardButton("Oᴡɴᴇʀ 👑", url="https://t.me/whosekirito")]
    ])

    try:
        if photo:
            await m.reply_photo(photo=photo, caption=caption, reply_markup=keyboard)
        else:
            await m.reply(text=caption, reply_markup=keyboard)
    except Exception as e:
        print(f"Error sending start message: {e}")
        await m.reply(text=caption, reply_markup=keyboard)

@app.on_message(filters.command("help") & filters.private)
async def help_command(_, m: Message):
    if not m.from_user:
        return
    if m.from_user.id not in approved_users:
        return await m.reply("🚫 You're not approved to use this bot.")

    help_text = """
📋 **Available Commands:**

**For All Users:**
• `/start` - Start the bot
• `/help` - Show this help message
• `/report` - Report a specific message
• `/reportchat @username` - Report a channel/group
• `/join <invite_link>` - Join a chat with all accounts

**Owner Only:**
• `/login` - Add new account using string session
• `/approve <user_id>` - Approve user
• `/unapprove <user_id>` - Remove user approval
• `/addproxy ip:port` - Add proxy for accounts
• `/broadcast <message>` - Send message to all users

**How to use:**
1. Use `/report` and send a message link
2. Choose report reason from buttons
3. Add comment or type 'skip'
4. Bot will report with all logged accounts (with random delays)
    """

    await m.reply(help_text)

@app.on_message(filters.command("approve") & filters.user(OWNER_ID))
async def approve(_, m: Message):
    if len(m.command) > 1:
        # Direct user ID approval: /approve 123456789
        try:
            uid = int(m.command[1])
            approved_users.add(uid)
            await save_approved_user(uid)
            await m.reply(f"✅ Approved user {uid}")
        except ValueError:
            await m.reply("❌ Invalid user ID format")
    elif m.reply_to_message and m.reply_to_message.from_user:
        # Reply approval
        uid = m.reply_to_message.from_user.id
        approved_users.add(uid)
        await save_approved_user(uid)
        await m.reply(f"✅ Approved user {uid}")
    else:
        await m.reply("❌ Usage: /approve <user_id> or reply to user's message")

@app.on_message(filters.command("unapprove") & filters.user(OWNER_ID))
async def unapprove(_, m: Message):
    if len(m.command) > 1:
        # Direct user ID unapproval: /unapprove 123456789
        try:
            uid = int(m.command[1])
            approved_users.discard(uid)
            await remove_approved_user(uid)
            await m.reply(f"❌ Unapproved user {uid}")
        except ValueError:
            await m.reply("❌ Invalid user ID format")
    elif m.reply_to_message and m.reply_to_message.from_user:
        # Reply unapproval
        uid = m.reply_to_message.from_user.id
        approved_users.discard(uid)
        await remove_approved_user(uid)
        await m.reply(f"❌ Unapproved user {uid}")
    else:
        await m.reply("❌ Usage: /unapprove <user_id> or reply to user's message")

@app.on_message(filters.command("addproxy") & filters.user(OWNER_ID))
async def add_proxy(_, m: Message):
    if len(m.command) < 2:
        return await m.reply("❌ Usage: /addproxy ip:port")

    proxy = m.text.split(None, 1)[1].strip()
    if ":" in proxy:
        proxy_list.append(proxy)
        await m.reply("✅ Proxy added.")
    else:
        await m.reply("❌ Invalid proxy format. Use: ip:port")

@app.on_message(filters.command("broadcast") & filters.user(OWNER_ID))
async def broadcast(_, m: Message):
    if len(m.command) < 2:
        return await m.reply("❌ Usage: /broadcast <message>")
    msg = m.text.split(None, 1)[1]
    success = 0
    for user_id in approved_users:
        try:
            await app.send_message(user_id, msg)
            success += 1
        except:
            continue
    await m.reply(f"✅ Broadcast sent to {success}/{len(approved_users)} users")

@app.on_message(filters.command("login") & filters.private)
async def login_cmd(_, m: Message):
    if m.from_user.id not in approved_users:
        return await m.reply("🚫 Not approved.")
    await m.reply("📱 Send your Telethon string session to add account")
    waiting_for_session[m.from_user.id] = True

@app.on_message(filters.command("join") & filters.private)
async def join(_, m: Message):
    if m.from_user.id not in approved_users:
        return await m.reply("🚫 Not approved.")
    if len(m.command) < 2:
        return await m.reply("❌ Usage: /join <invite_link_or_username>")

    target = m.command[1]
    if not accounts:
        return await m.reply("❌ No accounts logged in. Contact owner to add accounts.")

    success = 0
    failed = 0

    for acc in accounts:
        try:
            # Handle different formats: invite links, usernames, or chat IDs
            if "t.me/" in target:
                # Extract username or invite hash from link
                if "joinchat/" in target or "+" in target:
                    # Invite link
                    await acc["client"].join_chat(target)
                else:
                    # Username link like t.me/username
                    username = target.split("/")[-1]
                    await acc["client"].join_chat(username)
            elif target.startswith("@"):
                # Username with @
                await acc["client"].join_chat(target)
            elif target.startswith("-100") or target.isdigit():
                # Chat ID
                await acc["client"].join_chat(int(target))
            else:
                # Plain username
                await acc["client"].join_chat(target)
            success += 1
        except Exception as e:
            print(f"Error joining chat with account: {e}")
            failed += 1
            continue

    await m.reply(f"✅ Join Results:\n• Success: {success} accounts\n• Failed: {failed} accounts\n• Total: {len(accounts)} accounts")

@app.on_message(filters.command("report") & filters.private)
async def report(_, m: Message):
    if m.from_user.id not in approved_users:
        return await m.reply("🚫 Not approved.")
    if not accounts:
        return await m.reply("❌ No accounts logged in. Contact owner to add accounts.")

    await m.reply("📎 Send a message link from group/channel to report (format: t.me/username/123)")
    waiting_for_link[m.from_user.id] = True

@app.on_message(filters.command("reportchat") & filters.private)
async def report_chat(_, m: Message):
    if m.from_user.id not in approved_users:
        return await m.reply("🚫 Not approved.")
    if not accounts:
        return await m.reply("❌ No accounts logged in. Contact owner to add accounts.")
    if len(m.command) < 2:
        return await m.reply("❌ Usage: /reportchat @username or /reportchat -1001234567890")

    target = m.command[1]

    # Handle different formats
    if target.startswith("@"):
        chat_identifier = target[1:]  # Remove @
    elif target.startswith("-100") or target.isdigit() or target.startswith("-"):
        chat_identifier = target  # Keep chat ID as is
    elif "t.me/" in target:
        # Extract username from link
        chat_identifier = target.split("/")[-1]
    else:
        chat_identifier = target

    await m.reply("Select reason:", reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("Child Abuse", callback_data=f"rc:{chat_identifier}:child_abuse")],
        [InlineKeyboardButton("Violence", callback_data=f"rc:{chat_identifier}:violence")],
        [InlineKeyboardButton("Drugs", callback_data=f"rc:{chat_identifier}:drugs")],
        [InlineKeyboardButton("Weapons", callback_data=f"rc:{chat_identifier}:weapons")],
        [InlineKeyboardButton("Phone Leak", callback_data=f"rc:{chat_identifier}:phone")],
        [InlineKeyboardButton("Images Leak", callback_data=f"rc:{chat_identifier}:images")],
        [InlineKeyboardButton("Address Leak", callback_data=f"rc:{chat_identifier}:address")],
        [InlineKeyboardButton("Spam", callback_data=f"rc:{chat_identifier}:spam")],
    ]))

# Handle text messages for different states
@app.on_message(filters.private & filters.text & ~filters.command("start") & ~filters.command("help") & ~filters.command("approve") & ~filters.command("unapprove") & ~filters.command("addproxy") & ~filters.command("broadcast") & ~filters.command("login") & ~filters.command("join") & ~filters.command("report") & ~filters.command("reportchat"))
async def handle_text_messages(_, m: Message):
    user_id = m.from_user.id

    # Handle waiting for string session
    if user_id in waiting_for_session:
        await handle_string_session(m)
        return

    # Handle waiting for message link
    if user_id in waiting_for_link:
        await handle_message_link(m)
        return

    # Handle waiting for comment
    if user_id in waiting_for_comment:
        await handle_comment_input(m)
        return

async def handle_string_session(m: Message):
    user_id = m.from_user.id
    session_string = m.text.strip()

    try:
        # Try to connect with the session string
        client = TClient(StringSession(session_string), API_ID, API_HASH)
        await client.connect()

        if await client.is_user_authorized():
            # Save session to database
            await save_session_to_db(user_id, session_string)

            accounts.append({
                "client": client,
                "user_id": user_id
            })

            await m.reply("✅ Account added successfully!")
            del waiting_for_session[user_id]
        else:
            await m.reply("❌ Invalid session string - not authorized")
            await client.disconnect()
    except Exception as e:
        await m.reply(f"❌ Error adding account: {e}")
        if 'client' in locals():
            try:
                await client.disconnect()
            except:
                pass

    if user_id in waiting_for_session:
        del waiting_for_session[user_id]

async def handle_message_link(m: Message):
    user_id = m.from_user.id

    # Handle different link formats
    link = m.text.strip()

    # Pattern for t.me/c/channel_id/message_id (private channels)
    private_match = re.search(r"t\.me/c/(-?\d+)/(\d+)", link)
    # Pattern for t.me/username/message_id (public channels/groups)
    public_match = re.search(r"t\.me/(\w+)/(\d+)", link)

    if private_match:
        chat_id = f"-100{private_match.group(1)}"
        msg_id = int(private_match.group(2))
    elif public_match:
        chat_id = public_match.group(1)
        msg_id = int(public_match.group(2))
    else:
        return await m.reply("❌ Invalid link format. Examples:\n• t.me/username/123\n• t.me/c/1234567890/123")

    del waiting_for_link[user_id]  # Remove from waiting state

    await m.reply("Select report reason:", reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("Child Abuse", callback_data=f"rm:{chat_id}:{msg_id}:child_abuse")],
        [InlineKeyboardButton("Violence", callback_data=f"rm:{chat_id}:{msg_id}:violence")],
        [InlineKeyboardButton("Drugs", callback_data=f"rm:{chat_id}:{msg_id}:drugs")],
        [InlineKeyboardButton("Weapons", callback_data=f"rm:{chat_id}:{msg_id}:weapons")],
        [InlineKeyboardButton("Phone Leak", callback_data=f"rm:{chat_id}:{msg_id}:phone")],
        [InlineKeyboardButton("Images Leak", callback_data=f"rm:{chat_id}:{msg_id}:images")],
        [InlineKeyboardButton("Address Leak", callback_data=f"rm:{chat_id}:{msg_id}:address")],
        [InlineKeyboardButton("Spam", callback_data=f"rm:{chat_id}:{msg_id}:spam")],
    ]))

async def handle_comment_input(m: Message):
    user_id = m.from_user.id
    comment_data = waiting_for_comment[user_id]
    comment = m.text if m.text.lower() != "skip" else ""
    del waiting_for_comment[user_id]  # Remove from waiting state

    if comment_data["type"] == "message":
        chat_id = comment_data["chat"]
        msg_id = comment_data["msg_id"]
        reason = comment_data["reason"]

        success = 0
        total_accounts = len(accounts)

        await m.reply(f"🔄 Starting to report message with {total_accounts} accounts...")

        for i, acc in enumerate(accounts):
            try:
                # Random delay between 10-120 seconds
                delay = random.randint(10, 120)
                await asyncio.sleep(delay)

                # Handle both username and chat ID formats
                if chat_id.startswith('-100') or chat_id.isdigit() or chat_id.startswith('-'):
                    peer = int(chat_id)
                else:
                    peer = chat_id

                await acc["client"](ReportRequest(peer=peer, id=[int(msg_id)], reason=get_report_reason(reason), message=comment))
                success += 1

                # Progress update every 5 accounts
                if (i + 1) % 5 == 0:
                    await m.reply(f"⏳ Progress: {success}/{i+1} accounts completed...")

            except Exception as e:
                print(f"Error reporting message with account: {e}")
                continue

        await m.reply(f"✅ Message reported by {success}/{total_accounts} accounts")

    elif comment_data["type"] == "chat":
        username = comment_data["username"]
        reason = comment_data["reason"]

        success = 0
        total_accounts = len(accounts)

        await m.reply(f"🔄 Starting to report chat with {total_accounts} accounts...")

        for i, acc in enumerate(accounts):
            try:
                # Random delay between 10-120 seconds
                delay = random.randint(10, 120)
                await asyncio.sleep(delay)

                # Handle both username and chat ID formats for peer reporting
                if username.startswith('-100') or username.isdigit() or username.startswith('-'):
                    peer = int(username)
                else:
                    peer = username

                await acc["client"](ReportPeerRequest(peer=peer, reason=get_report_reason(reason), message=comment))
                success += 1

                # Progress update every 5 accounts
                if (i + 1) % 5 == 0:
                    await m.reply(f"⏳ Progress: {success}/{i+1} accounts completed...")

            except Exception as e:
                print(f"Error reporting chat with account: {e}")
                continue

        await m.reply(f"✅ Channel/Group reported by {success}/{total_accounts} accounts")

@app.on_callback_query()
async def handle_callbacks(_, cb):
    data = cb.data
    user_id = cb.from_user.id

    if data.startswith("rm:"):
        _, chat, msg_id, reason = data.split(":")
        await cb.message.edit("💬 Enter comment (or type 'skip' for no comment):")
        waiting_for_comment[user_id] = {
            "type": "message",
            "chat": chat,
            "msg_id": msg_id,
            "reason": reason
        }

    elif data.startswith("rc:"):
        _, username, reason = data.split(":")
        await cb.message.edit("💬 Enter comment (or type 'skip' for no comment):")
        waiting_for_comment[user_id] = {
            "type": "chat",
            "username": username,
            "reason": reason
        }

async def startup():
    """Initialize bot and load data from MongoDB"""
    print("[INFO] Loading data from MongoDB...")
    await load_data_from_db()
    print(f"[INFO] Loaded {len(approved_users)} approved users and {len(accounts)} accounts")
    print("[INFO] Kirito Report Bot is running...")

if __name__ == "__main__":
    # Run startup function before starting the bot
    asyncio.get_event_loop().run_until_complete(startup())
    app.run()
