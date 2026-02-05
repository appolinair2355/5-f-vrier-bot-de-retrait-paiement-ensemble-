import os
import asyncio
import re
import logging
import sys
import json
import random
from datetime import datetime, timedelta, timezone, time
from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession
from telethon.tl.functions.channels import EditBannedRequest
from telethon.tl.types import ChatBannedRights
from aiohttp import web
from config import (
    API_ID, API_HASH, BOT_TOKEN, ADMIN_ID,
    PORT, SUIT_MAPPING, ALL_SUITS, SUIT_DISPLAY
)

PAYMENT_LINK = "https://my.moneyfusion.net/6977f7502181d4ebf722398d"
USERS_FILE = "users_data.json"
PAUSE_CONFIG_FILE = "pause_config.json"
VIP_CONFIG_FILE = "vip_config.json"
CHANNELS_CONFIG_FILE = "channels_config.json"
TRIAL_CONFIG_FILE = "trial_config.json"

# Configuration par défaut des canaux
DEFAULT_SOURCE_CHANNEL_ID = -1002682552255
DEFAULT_PREDICTION_CHANNEL_ID = -1003502536129
DEFAULT_VIP_CHANNEL_ID = -1003502536129
DEFAULT_VIP_CHANNEL_LINK = "https://t.me/+3pHxyUtjt34zMzg0"

# --- Configuration et Initialisation ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

if not API_ID or API_ID == 0:
    logger.error("API_ID manquant")
    exit(1)
if not API_HASH:
    logger.error("API_HASH manquant")
    exit(1)
if not BOT_TOKEN:
    logger.error("BOT_TOKEN manquant")
    exit(1)

session_string = os.getenv('TELEGRAM_SESSION', '')
client = TelegramClient(StringSession(session_string), API_ID, API_HASH)

# --- Variables Globales ---
channels_config = {
    'source_channel_id': DEFAULT_SOURCE_CHANNEL_ID,
    'prediction_channel_id': DEFAULT_PREDICTION_CHANNEL_ID,
    'vip_channel_id': DEFAULT_VIP_CHANNEL_ID,
    'vip_channel_link': DEFAULT_VIP_CHANNEL_LINK
}

DEFAULT_PAUSE_CYCLE = [180, 240, 420]
pause_config = {
    'cycle': DEFAULT_PAUSE_CYCLE.copy(),
    'current_index': 0,
    'last_prediction_time': None,
    'predictions_count': 0,
    'is_paused': False,
    'pause_end_time': None,
    'just_resumed': False
}

DEFAULT_TRIAL_DURATION = 15
trial_config = {
    'duration_minutes': DEFAULT_TRIAL_DURATION,
    'link_visible_seconds': 10
}

pending_predictions = {}
processed_messages = set()
current_game_number = 0
last_source_game_number = 0
last_predicted_number = None

verification_state = {
    'predicted_number': None,
    'predicted_suit': None,
    'current_check': 0,
    'message_id': None,
    'channel_id': None,
    'status': None
}

SUIT_CYCLE = ['♥', '♦', '♣', '♠', '♦', '♥', '♠', '♣']
already_predicted_games = set()
stats_bilan = {
    'total': 0, 'wins': 0, 'losses': 0,
    'win_details': {'✅0️⃣': 0, '✅1️⃣': 0, '✅2️⃣': 0, '✅3️⃣': 0},
    'loss_details': {'❌': 0}
}

users_data = {}
user_conversation_state = {}
pending_payments = {}
admin_setting_time = {}
watch_state = {}

predictions_enabled = True
pending_finalization = {}

# ============================================================
# FONCTIONS DE CHARGEMENT/SAUVEGARDE
# ============================================================

def load_json(file_path, default=None):
    try:
        if os.path.exists(file_path):
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"Erreur chargement {file_path}: {e}")
    return default or {}

def save_json(file_path, data):
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Erreur sauvegarde {file_path}: {e}")

def load_all_configs():
    global channels_config, pause_config, trial_config, users_data
    channels_config.update(load_json(CHANNELS_CONFIG_FILE, channels_config))
    pause_config.update(load_json(PAUSE_CONFIG_FILE, pause_config))
    trial_config.update(load_json(TRIAL_CONFIG_FILE, trial_config))
    users_data.update(load_json(USERS_FILE, {}))

def save_all_configs():
    save_json(CHANNELS_CONFIG_FILE, channels_config)
    save_json(PAUSE_CONFIG_FILE, pause_config)
    save_json(TRIAL_CONFIG_FILE, trial_config)
    save_json(USERS_FILE, users_data)

# ============================================================
# GESTION DES NUMÉROS
# ============================================================

def get_valid_even_numbers():
    return [num for num in range(6, 1437) if num % 2 == 0 and num % 10 != 0]

VALID_EVEN_NUMBERS = get_valid_even_numbers()

def get_suit_for_number(number):
    if number not in VALID_EVEN_NUMBERS:
        return None
    return SUIT_CYCLE[VALID_EVEN_NUMBERS.index(number) % len(SUIT_CYCLE)]

def is_trigger_number(number):
    """Déclencheur = impair finissant par 1,3,5,7 et suivant est pair valide"""
    if number % 2 == 0:
        return False
    last_digit = number % 10
    if last_digit not in [1, 3, 5, 7]:
        return False
    return (number + 1) in VALID_EVEN_NUMBERS

def get_trigger_target(number):
    if not is_trigger_number(number):
        return None
    return number + 1

# ============================================================
# GESTION UTILISATEURS
# ============================================================

def get_user(user_id: int) -> dict:
    user_id_str = str(user_id)
    if user_id_str not in users_data:
        users_data[user_id_str] = {
            'registered': False, 'nom': None, 'prenom': None, 'pays': None,
            'trial_started': None, 'trial_used': False, 'trial_joined_at': None,
            'subscription_end': None, 'vip_expires_at': None, 'is_in_channel': False,
            'total_time_added': 0, 'pending_payment': False, 'awaiting_screenshot': False
        }
        save_json(USERS_FILE, users_data)
    return users_data[user_id_str]

def update_user(user_id: int, data: dict):
    users_data[str(user_id)].update(data)
    save_json(USERS_FILE, users_data)

def is_user_subscribed(user_id: int) -> bool:
    if user_id == ADMIN_ID:
        return True
    user = get_user(user_id)
    if not user.get('subscription_end'):
        return False
    try:
        return datetime.now() < datetime.fromisoformat(user['subscription_end'])
    except:
        return False

def is_trial_active(user_id: int) -> bool:
    if user_id == ADMIN_ID:
        return True
    user = get_user(user_id)
    if user.get('trial_used') or not user.get('trial_joined_at'):
        return False
    try:
        trial_end = datetime.fromisoformat(user['trial_joined_at']) + timedelta(minutes=trial_config['duration_minutes'])
        return datetime.now() < trial_end
    except:
        return False

def get_remaining_time(user_id: int) -> str:
    if user_id == ADMIN_ID:
        return "∞"
    user = get_user(user_id)
    if is_user_subscribed(user_id):
        return format_time_remaining(user['subscription_end'])
    elif is_trial_active(user_id):
        trial_end = datetime.fromisoformat(user['trial_joined_at']) + timedelta(minutes=trial_config['duration_minutes'])
        remaining = int((trial_end - datetime.now()).total_seconds())
        return format_seconds(remaining)
    return "Expiré"

def format_time_remaining(expiry_iso: str) -> str:
    try:
        expiry = datetime.fromisoformat(expiry_iso)
        remaining = expiry - datetime.now()
        if remaining.total_seconds() <= 0:
            return "Expiré"
        total_seconds = int(remaining.total_seconds())
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60
        parts = []
        if hours > 0:
            parts.append(f"{hours}h")
        if minutes > 0:
            parts.append(f"{minutes}m")
        parts.append(f"{seconds}s")
        return " ".join(parts)
    except:
        return "Inconnu"

def format_seconds(seconds: int) -> str:
    if seconds <= 0:
        return "Expiré"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    parts = []
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")
    if secs > 0 or (hours == 0 and minutes == 0):
        parts.append(f"{secs}s")
    return " ".join(parts)

def parse_duration(input_str: str) -> int:
    input_str = input_str.strip().lower()
    if input_str.isdigit():
        return int(input_str)
    if input_str.endswith('h'):
        try:
            return int(float(input_str[:-1]) * 60)
        except:
            return 0
    if input_str.endswith('m'):
        try:
            return int(input_str[:-1])
        except:
            return 0
    return 0

# ============================================================
# GESTION CANAUX
# ============================================================

def get_source_channel_id():
    return channels_config.get('source_channel_id', DEFAULT_SOURCE_CHANNEL_ID)

def get_prediction_channel_id():
    return channels_config.get('prediction_channel_id', DEFAULT_PREDICTION_CHANNEL_ID)

def get_vip_channel_id():
    return channels_config.get('vip_channel_id', DEFAULT_VIP_CHANNEL_ID)

def get_vip_channel_link():
    return channels_config.get('vip_channel_link', DEFAULT_VIP_CHANNEL_LINK)

def set_channels(source_id=None, prediction_id=None, vip_id=None, vip_link=None):
    if source_id:
        channels_config['source_channel_id'] = source_id
    if prediction_id:
        channels_config['prediction_channel_id'] = prediction_id
    if vip_id:
        channels_config['vip_channel_id'] = vip_id
    if vip_link:
        channels_config['vip_channel_link'] = vip_link
    save_json(CHANNELS_CONFIG_FILE, channels_config)

# ============================================================
# GESTION VIP ET ESSAI
# ============================================================

async def delete_message_after_delay(chat_id: int, message_id: int, delay_seconds: int):
    await asyncio.sleep(delay_seconds)
    try:
        await client.delete_messages(chat_id, [message_id])
    except:
        pass

async def add_user_to_vip(user_id: int, duration_minutes: int, is_trial: bool = False):
    """Ajoute un utilisateur au VIP avec notification complète"""
    if user_id == ADMIN_ID:
        return True
        
    try:
        now = datetime.now()
        expires_at = now + timedelta(minutes=duration_minutes)
        
        update_user(user_id, {
            'vip_joined_at': now.isoformat(),
            'vip_expires_at': expires_at.isoformat(),
            'subscription_end': expires_at.isoformat(),
            'is_in_channel': True,
            'total_time_added': get_user(user_id).get('total_time_added', 0) + duration_minutes,
            'pending_payment': False,
            'awaiting_screenshot': False
        })
        
        if is_trial:
            update_user(user_id, {'trial_joined_at': now.isoformat()})
        
        time_str = format_time_remaining(expires_at.isoformat())
        vip_link = get_vip_channel_link()
        
        # Message à l'utilisateur
        link_msg = await client.send_message(user_id, f"""🎉 **{'ESSAI GRATUIT' if is_trial else 'ABONNEMENT'} ACTIVÉ!** 🎉

✅ **Accès VIP confirmé!**
⏳ **Temps restant:** {time_str}
📅 **Expire le:** {expires_at.strftime('%d/%m/%Y à %H:%M')}

🔗 **Lien du canal VIP:**
{vip_link}

⚠️ **IMPORTANT:**
• Ce lien disparaîtra dans **10 secondes**!
• Rejoignez **IMMÉDIATEMENT**!
• Vous serez retiré automatiquement à l'expiration

🚀 **Bonne chance!**""")
        
        # Suppression après 10 secondes
        asyncio.create_task(delete_message_after_delay(user_id, link_msg.id, 10))
        
        # Notification admin
        user = get_user(user_id)
        await client.send_message(ADMIN_ID, f"""✅ **{'ESSAI' if is_trial else 'ABONNEMENT'} ACTIVÉ**

👤 **Nom:** {user.get('prenom', '')} {user.get('nom', '')}
🆔 **ID:** `{user_id}`
⏱️ **Durée:** {duration_minutes} minutes
⏳ **Expire:** {time_str}
📊 **Total:** {user.get('total_time_added', 0)} min""")
        
        # Programmation exclusion
        asyncio.create_task(auto_kick_user(user_id, duration_minutes * 60))
        return True
        
    except Exception as e:
        logger.error(f"Erreur ajout VIP {user_id}: {e}")
        return False

async def auto_kick_user(user_id: int, delay_seconds: int):
    """Expulse un utilisateur après le délai"""
    if user_id == ADMIN_ID:
        return
        
    await asyncio.sleep(delay_seconds)
    
    try:
        user = get_user(user_id)
        if is_user_subscribed(user_id):  # A renouvelé entre-temps
            return
            
        entity = await client.get_input_entity(get_vip_channel_id())
        await client.kick_participant(entity, user_id)
        await client(EditBannedRequest(
            channel=entity, participant=user_id,
            banned_rights=ChatBannedRights(until_date=None, view_messages=False)
        ))
        
        update_user(user_id, {
            'vip_expires_at': None, 'subscription_end': None,
            'is_in_channel': False, 'trial_used': True
        })
        
        await client.send_message(user_id, """⏰ **VOTRE ACCÈS EST TERMINÉ**

💳 Pour réintégrer le canal:
/payer""")
        
        await client.send_message(ADMIN_ID, f"""🚫 **UTILISATEUR RETIRÉ**

🆔 **ID:** `{user_id}`
👤 **Nom:** {user.get('prenom', '')} {user.get('nom', '')}""")
        
    except Exception as e:
        logger.error(f"Erreur expulsion {user_id}: {e}")

# ============================================================
# SYSTÈME DE PRÉDICTION
# ============================================================

async def send_prediction(target_game, predicted_suit, base_game):
    global verification_state, last_predicted_number
    
    if not predictions_enabled:
        return False
    
    if verification_state['predicted_number'] is not None:
        logger.warning(f"⛔ Bloqué: #{verification_state['predicted_number']} en cours")
        return False
    
    try:
        entity = await client.get_input_entity(get_prediction_channel_id())
        
        sent_msg = await client.send_message(entity, f"""🎰 **PRÉDICTION #{target_game}**
🎯 Couleur: {SUIT_DISPLAY.get(predicted_suit, predicted_suit)}
⏳ Statut: EN ATTENTE...""")
        
        verification_state = {
            'predicted_number': target_game,
            'predicted_suit': predicted_suit,
            'current_check': 0,
            'message_id': sent_msg.id,
            'channel_id': get_prediction_channel_id(),
            'status': 'pending'
        }
        
        last_predicted_number = target_game
        pause_config['predictions_count'] += 1
        save_json(PAUSE_CONFIG_FILE, pause_config)
        
        logger.info(f"✅ Prédiction #{target_game} lancée")
        return True
        
    except Exception as e:
        logger.error(f"Erreur envoi prédiction: {e}")
        return False

async def update_prediction_status(status):
    global verification_state, stats_bilan
    
    if verification_state['predicted_number'] is None:
        return False
    
    try:
        predicted_num = verification_state['predicted_number']
        suit = verification_state['predicted_suit']
        
        status_text = "❌ PERDU" if status == "❌" else f"{status} GAGNÉ"
        
        await client.edit_message(
            verification_state['channel_id'],
            verification_state['message_id'],
            f"""🎰 **PRÉDICTION #{predicted_num}**
🎯 Couleur: {SUIT_DISPLAY.get(suit, suit)}
📊 Statut: {status_text}"""
        )
        
        # Stats
        if status in ['✅0️⃣', '✅1️⃣', '✅2️⃣', '✅3️⃣']:
            stats_bilan['total'] += 1
            stats_bilan['wins'] += 1
            stats_bilan['win_details'][status] = stats_bilan['win_details'].get(status, 0) + 1
        elif status == '❌':
            stats_bilan['total'] += 1
            stats_bilan['losses'] += 1
        
        logger.info(f"🔓 Prédiction #{predicted_num} terminée: {status}")
        
        # Libération
        verification_state = {
            'predicted_number': None, 'predicted_suit': None,
            'current_check': 0, 'message_id': None,
            'channel_id': None, 'status': None
        }
        return True
        
    except Exception as e:
        logger.error(f"Erreur mise à jour statut: {e}")
        return False

# ============================================================
# ANALYSE MESSAGES SOURCE
# ============================================================

def extract_game_number(message):
    for pattern in [r"#N\s*(\d+)", r"^#(\d+)", r"N\s*(\d+)"]:
        match = re.search(pattern, message, re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None

def extract_suits(message_text):
    matches = re.findall(r"\(([^)]+)\)", message_text)
    if not matches:
        return []
    
    normalized = matches[0].replace('❤️', '♥').replace('❤', '♥').replace('♥️', '♥')
    normalized = normalized.replace('♠️', '♠').replace('♦️', '♦').replace('♣️', '♣')
    
    return [s for s in ['♥', '♠', '♦', '♣'] if s in normalized]

def is_finalized(message_text):
    return '✅' in message_text or '🔰' in message_text

def is_editing(message_text):
    return message_text.strip().startswith('⏰')

async def process_verification(game_number, message_text):
    global verification_state
    
    if verification_state['predicted_number'] is None:
        return
    
    predicted_num = verification_state['predicted_number']
    predicted_suit = verification_state['predicted_suit']
    current_check = verification_state['current_check']
    
    suits = extract_suits(message_text)
    
    # Gagné
    if predicted_suit in suits:
        await update_prediction_status(f"✅{current_check}️⃣")
        return
    
    # Perdu après 4 essais
    if current_check >= 3:
        await update_prediction_status("❌")
        return
    
    # Continuer
    verification_state['current_check'] += 1
    logger.info(f"❌ Check {current_check} échoué, prochain: #{predicted_num + verification_state['current_check']}")

async def check_and_launch_prediction(game_number):
    """Vérifie et lance une prédiction si conditions réunies"""
    
    # Bloquer si prédiction en cours
    if verification_state['predicted_number'] is not None:
        logger.warning(f"⛔ Bloqué: attente #{verification_state['predicted_number']}")
        return
    
    if not is_trigger_number(game_number):
        return
    
    target_num = get_trigger_target(game_number)
    if not target_num or target_num in already_predicted_games:
        return
    
    # Pause auto après 5 prédictions
    if pause_config['predictions_count'] >= 5:
        duration = pause_config['cycle'][pause_config['current_index'] % len(pause_config['cycle'])]
        pause_config['is_paused'] = True
        pause_config['pause_end_time'] = (datetime.now() + timedelta(seconds=duration)).isoformat()
        pause_config['predictions_count'] = 0
        pause_config['current_index'] += 1
        save_json(PAUSE_CONFIG_FILE, pause_config)
        
        await client.send_message(get_prediction_channel_id(), 
            f"⏸️ **PAUSE AUTO**\nRetour dans {duration//60} minutes...")
        return
    
    # Vérifier pause active
    if pause_config['is_paused']:
        try:
            end_time = datetime.fromisoformat(pause_config['pause_end_time'])
            if datetime.now() < end_time:
                return
            pause_config['is_paused'] = False
            save_json(PAUSE_CONFIG_FILE, pause_config)
        except:
            pause_config['is_paused'] = False
    
    suit = get_suit_for_number(target_num)
    if suit and await send_prediction(target_num, suit, game_number):
        already_predicted_games.add(target_num)

async def process_source_message(event, is_edit=False):
    global current_game_number, last_source_game_number, pending_finalization
    
    try:
        message_text = event.message.message
        game_number = extract_game_number(message_text)
        
        if game_number is None:
            return
        
        logger.info(f"📩 Message {'édité' if is_edit else 'reçu'}: #{game_number}")
        
        # ÉTAPE 1: Vérification prioritaire
        if verification_state['predicted_number'] is not None:
            predicted_num = verification_state['predicted_number']
            expected = predicted_num + verification_state['current_check']
            
            if game_number == expected:
                await process_verification(game_number, message_text)
            
            # Ne jamais lancer si vérification en cours
            return
        
        # ÉTAPE 2: Nouveau lancement
        if not pause_config.get('is_paused', False):
            await check_and_launch_prediction(game_number)
        
        # Suivi
        if is_editing(message_text):
            pending_finalization[game_number] = message_text
        else:
            if game_number in pending_finalization:
                del pending_finalization[game_number]
            current_game_number = game_number
            last_source_game_number = game_number
            
    except Exception as e:
        logger.error(f"Erreur traitement message: {e}")

# ============================================================
# COMMANDES UTILISATEURS
# ============================================================

@client.on(events.NewMessage(pattern='/start'))
async def cmd_start(event):
    if event.is_group or event.is_channel:
        return
    
    user_id = event.sender_id
    
    if user_id == ADMIN_ID:
        await event.respond("""👑 **ADMINISTRATEUR**

Commandes disponibles:
/predictinfo - Statut prédictions
/stop /resume - Contrôle
/users - Liste utilisateurs
/monitor - Temps restant
/watch - Surveillance temps réel
/setchannel - Configurer canaux
/bilan - Statistiques""")
        return
    
    user = get_user(user_id)
    
    if user.get('registered'):
        await event.respond(f"""👋 Bonjour {user.get('prenom', '')}!

📊 Statut: {'✅ Abonné' if is_user_subscribed(user_id) else '🎁 Essai' if is_trial_active(user_id) else '❌ Inactif'}
⏳ Temps: {get_remaining_time(user_id)}

💡 /payer pour renouveler""")
        return
    
    user_conversation_state[user_id] = 'awaiting_nom'
    await event.respond("""👋 **BIENVENUE!**

📝 **Étape 1/3:** Votre nom de famille?""")

@client.on(events.NewMessage(pattern='/payer'))
async def cmd_payer(event):
    if event.is_group or event.is_channel:
        return
    
    user_id = event.sender_id
    if user_id == ADMIN_ID:
        await event.respond("👑 Vous avez un accès illimité.")
        return
    
    user = get_user(user_id)
    if not user.get('registered'):
        await event.respond("❌ Inscrivez-vous avec /start")
        return
    
    buttons = [[Button.url("💳 PAYER MAINTENANT", PAYMENT_LINK)]]
    
    await event.respond("""💳 **PAIEMENT**

1️⃣ Cliquez sur le bouton ci-dessous
2️⃣ Effectuez le paiement
3️⃣ Envoyez la capture d'écran ici
4️⃣ L'admin valide et vous recevez l'accès

⚠️ Le lien d'accès disparaît après 10 secondes, rejoignez immédiatement!""", buttons=buttons)
    
    update_user(user_id, {'awaiting_screenshot': True})

@client.on(events.NewMessage(pattern='/status'))
async def cmd_status(event):
    if event.is_group or event.is_channel:
        return
    
    user_id = event.sender_id
    user = get_user(user_id)
    
    if not user.get('registered'):
        await event.respond("❌ /start pour vous inscrire")
        return
    
    await event.respond(f"""📊 **VOTRE STATUT**

👤 {user.get('prenom', '')} {user.get('nom', '')}
🌍 {user.get('pays', 'N/A')}
⏳ {get_remaining_time(user_id)}

💡 /payer pour renouveler""")

# ============================================================
# COMMANDES ADMIN
# ============================================================

@client.on(events.NewMessage(pattern='/stop'))
async def cmd_stop(event):
    if event.sender_id != ADMIN_ID:
        return
    global predictions_enabled
    predictions_enabled = False
    await event.respond("🛑 **PRÉDICTIONS ARRÊTÉES**")

@client.on(events.NewMessage(pattern='/resume'))
async def cmd_resume(event):
    if event.sender_id != ADMIN_ID:
        return
    global predictions_enabled, already_predicted_games, verification_state
    predictions_enabled = True
    already_predicted_games.clear()
    verification_state = {
        'predicted_number': None, 'predicted_suit': None,
        'current_check': 0, 'message_id': None,
        'channel_id': None, 'status': None
    }
    await event.respond("🚀 **PRÉDICTIONS REPRISES**")

@client.on(events.NewMessage(pattern='/predictinfo'))
async def cmd_predictinfo(event):
    if event.sender_id != ADMIN_ID:
        return
    
    verif = "Aucune"
    if verification_state['predicted_number']:
        verif = f"#{verification_state['predicted_number']} - Check {verification_state['current_check']}/3"
    
    await event.respond(f"""📊 **STATUT SYSTÈME**

🎯 Source: #{current_game_number}
🔍 Vérification: {verif}
⏸️ Pause: {'Oui' if pause_config['is_paused'] else 'Non'}
📊 Compteur: {pause_config['predictions_count']}/5

💡 /clearverif si bloqué""")

@client.on(events.NewMessage(pattern='/clearverif'))
async def cmd_clearverif(event):
    if event.sender_id != ADMIN_ID:
        return
    
    global verification_state
    old = verification_state['predicted_number']
    verification_state = {
        'predicted_number': None, 'predicted_suit': None,
        'current_check': 0, 'message_id': None,
        'channel_id': None, 'status': None
    }
    await event.respond(f"✅ {'Vérification #' + str(old) + ' effacée' if old else 'Aucune vérification'}")

@client.on(events.NewMessage(pattern='/users'))
async def cmd_users(event):
    if event.sender_id != ADMIN_ID:
        return
    
    if not users_data:
        await event.respond("📊 Aucun utilisateur")
        return
    
    lines = []
    for uid_str, info in users_data.items():
        uid = int(uid_str)
        status = "👑 ADMIN" if uid == ADMIN_ID else "✅" if is_user_subscribed(uid) else "🎁" if is_trial_active(uid) else "❌"
        lines.append(f"`{uid}` | {info.get('prenom', '')} {info.get('nom', '')} | {status} | {get_remaining_time(uid)}")
    
    # Envoyer par paquets de 50
    for i in range(0, len(lines), 50):
        await event.respond("\n".join(lines[i:i+50]))
        await asyncio.sleep(0.3)

@client.on(events.NewMessage(pattern='/monitor'))
async def cmd_monitor(event):
    if event.sender_id != ADMIN_ID:
        return
    
    active = []
    for uid_str, info in users_data.items():
        uid = int(uid_str)
        if is_user_subscribed(uid) or is_trial_active(uid):
            name = f"{info.get('prenom', '')} {info.get('nom', '')}"
            active.append(f"`{uid}` | {name[:20]} | ⏳ {get_remaining_time(uid)}")
    
    if not active:
        await event.respond("📊 Aucun utilisateur actif")
        return
    
    await event.respond("⏱️ **UTILISATEURS ACTIFS**\n\n" + "\n".join(active[:30]))

@client.on(events.NewMessage(pattern='/watch'))
async def cmd_watch(event):
    if event.sender_id != ADMIN_ID:
        return
    
    # Créer message de surveillance
    msg = await event.respond("⏱️ **SURVEILLANCE TEMPS RÉEL**\nDémarrage...")
    watch_state[event.sender_id] = {'msg_id': msg.id, 'active': True}
    
    asyncio.create_task(watch_loop(event.sender_id))

async def watch_loop(admin_id):
    while watch_state.get(admin_id, {}).get('active', False):
        await asyncio.sleep(30)  # Update every 30s
        
        try:
            # Construire liste
            lines = ["⏱️ **SURVEILLANCE TEMPS RÉEL**\n"]
            
            for uid_str, info in users_data.items():
                uid = int(uid_str)
                if is_user_subscribed(uid) or is_trial_active(uid):
                    name = f"{info.get('prenom', '')} {info.get('nom', '')}"
                    time_left = get_remaining_time(uid)
                    lines.append(f"`{uid}` | {name[:15]} | {time_left}")
            
            lines.append(f"\n🔄 Mis à jour: {datetime.now().strftime('%H:%M:%S')}")
            lines.append("💡 /stopwatch pour arrêter")
            
            await client.edit_message(admin_id, watch_state[admin_id]['msg_id'], "\n".join(lines[:35]))
            
        except Exception as e:
            logger.error(f"Erreur watch: {e}")
            break

@client.on(events.NewMessage(pattern='/stopwatch'))
async def cmd_stopwatch(event):
    if event.sender_id != ADMIN_ID:
        return
    watch_state[event.sender_id] = {'active': False}
    await event.respond("✅ Surveillance arrêtée")

@client.on(events.NewMessage(pattern=r'^/setchannel(\s+.+)?$'))
async def cmd_setchannel(event):
    if event.sender_id != ADMIN_ID:
        return
    
    parts = event.message.message.strip().split()
    
    if len(parts) < 3:
        await event.respond(f"""📺 **CONFIGURATION CANAUX**

**Actuel:**
Source: `{get_source_channel_id()}`
Prédiction: `{get_prediction_channel_id()}`
VIP: `{get_vip_channel_id()}`
Lien VIP: {get_vip_channel_link()}

**Usage:**
`/setchannel source -100123456`
`/setchannel prediction -100123456`
`/setchannel vip -100123456 https://t.me/...`""")
        return
    
    try:
        ctype = parts[1].lower()
        cid = int(parts[2])
        
        if ctype == 'source':
            set_channels(source_id=cid)
            await event.respond(f"✅ Canal source: `{cid}`")
        elif ctype == 'prediction':
            set_channels(prediction_id=cid)
            await event.respond(f"✅ Canal prédiction: `{cid}`")
        elif ctype == 'vip':
            if len(parts) < 4:
                await event.respond("❌ Fournissez aussi le lien")
                return
            set_channels(vip_id=cid, vip_link=parts[3])
            await event.respond(f"✅ Canal VIP: `{cid}`\nLien: {parts[3]}")
        else:
            await event.respond("❌ Type: source, prediction, ou vip")
            
    except Exception as e:
        await event.respond(f"❌ Erreur: {e}")

@client.on(events.NewMessage(pattern='/bilan'))
async def cmd_bilan(event):
    if event.sender_id != ADMIN_ID:
        return
    
    if stats_bilan['total'] == 0:
        await event.respond("📊 Aucune prédiction")
        return
    
    win_rate = (stats_bilan['wins'] / stats_bilan['total']) * 100
    
    await event.respond(f"""📊 **BILAN**

🎯 Total: {stats_bilan['total']}
✅ Victoires: {stats_bilan['wins']} ({win_rate:.1f}%)
❌ Défaites: {stats_bilan['losses']}

**Détails victoires:**
• Immédiat: {stats_bilan['win_details'].get('✅0️⃣', 0)}
• 2ème: {stats_bilan['win_details'].get('✅1️⃣', 0)}
• 3ème: {stats_bilan['win_details'].get('✅2️⃣', 0)}
• 4ème: {stats_bilan['win_details'].get('✅3️⃣', 0)}""")

@client.on(events.NewMessage(pattern='/reset'))
async def cmd_reset(event):
    if event.sender_id != ADMIN_ID:
        return
    
    global users_data, stats_bilan, already_predicted_games, verification_state
    
    users_data = {}
    save_json(USERS_FILE, {})
    
    stats_bilan = {'total': 0, 'wins': 0, 'losses': 0,
        'win_details': {'✅0️⃣': 0, '✅1️⃣': 0, '✅2️⃣': 0, '✅3️⃣': 0},
        'loss_details': {'❌': 0}}
    
    already_predicted_games.clear()
    verification_state = {
        'predicted_number': None, 'predicted_suit': None,
        'current_check': 0, 'message_id': None,
        'channel_id': None, 'status': None
    }
    
    await event.respond("🚨 **RESET EFFECTUÉ**")

# ============================================================
# GESTION MESSAGES ET PAIEMENTS
# ============================================================

@client.on(events.NewMessage)
async def handle_messages(event):
    # Messages canal source
    if event.is_group or event.is_channel:
        if event.chat_id == get_source_channel_id():
            await process_source_message(event)
        return
    
    # Commandes ignorées ici (gérées par décorateurs)
    if event.message.message.startswith('/'):
        return
    
    user_id = event.sender_id
    
    # Admin - gestion durée après validation paiement
    if user_id == ADMIN_ID and user_id in admin_setting_time:
        state = admin_setting_time[user_id]
        if state['step'] == 'awaiting_duration':
            duration_input = event.message.message.strip()
            target_id = state['target_user_id']
            
            minutes = parse_duration(duration_input)
            
            if minutes < 2:
                await event.respond("❌ Minimum 2 minutes")
                return
            if minutes > 45000:  # 750h
                await event.respond("❌ Maximum 750 heures")
                return
            
            del admin_setting_time[user_id]
            if target_id in pending_payments:
                del pending_payments[target_id]
            
            await add_user_to_vip(target_id, minutes, is_trial=False)
            return
    
    # Inscription conversation
    if user_id in user_conversation_state:
        state = user_conversation_state[user_id]
        text = event.message.message.strip()
        
        if state == 'awaiting_nom':
            update_user(user_id, {'nom': text})
            user_conversation_state[user_id] = 'awaiting_prenom'
            await event.respond("✅ **Étape 2/3:** Votre prénom?")
            return
            
        elif state == 'awaiting_prenom':
            update_user(user_id, {'prenom': text})
            user_conversation_state[user_id] = 'awaiting_pays'
            await event.respond("✅ **Étape 3/3:** Votre pays?")
            return
            
        elif state == 'awaiting_pays':
            update_user(user_id, {
                'pays': text, 'registered': True,
                'trial_started': datetime.now().isoformat()
            })
            del user_conversation_state[user_id]
            
            # Lancer essai
            await add_user_to_vip(user_id, trial_config['duration_minutes'], is_trial=True)
            await event.respond("""🎉 **INSCRIPTION RÉUSSIE!**

✅ Essai gratuit activé!
⏳ Durée: {} minutes

⚠️ Rejoignez vite le canal, le lien disparaît en 10 secondes!""".format(trial_config['duration_minutes']))
            return
    
    # Capture d'écran paiement
    user = get_user(user_id)
    if user.get('awaiting_screenshot') and event.message.photo:
        photo = event.message.photo
        pending_payments[user_id] = {'photo_id': photo.id, 'time': datetime.now().isoformat()}
        
        buttons = [
            [Button.inline("✅ Valider", data=f"validate_{user_id}")],
            [Button.inline("❌ Rejeter", data=f"reject_{user_id}")]
        ]
        
        await client.send_file(ADMIN_ID, photo, caption=f"""🔔 **NOUVEAU PAIEMENT**

🆔 ID: `{user_id}`
👤 {user.get('prenom', '')} {user.get('nom', '')}
🌍 {user.get('pays', 'N/A')}

⏰ {datetime.now().strftime('%H:%M:%S')}""", buttons=buttons)
        
        update_user(user_id, {'awaiting_screenshot': False})
        await event.respond("⏳ Paiement en cours de validation...")
        return

@client.on(events.CallbackQuery(data=re.compile(rb'validate_(\d+)')))
async def handle_validate(event):
    if event.sender_id != ADMIN_ID:
        await event.answer("❌", alert=True)
        return
    
    user_id = int(event.data_match.group(1).decode())
    
    if user_id not in pending_payments:
        await event.answer("Déjà traité", alert=True)
        return
    
    admin_setting_time[ADMIN_ID] = {
        'target_user_id': user_id,
        'step': 'awaiting_duration'
    }
    
    user = get_user(user_id)
    await event.edit(f"""✅ **VALIDATION**

🆔 `{user_id}`
👤 {user.get('prenom', '')} {user.get('nom', '')}

📝 **Durée d'abonnement?**
Ex: `120` (min), `2h`, `5h`, `750h`

Min: 2min | Max: 750h""")

@client.on(events.CallbackQuery(data=re.compile(rb'reject_(\d+)')))
async def handle_reject(event):
    if event.sender_id != ADMIN_ID:
        await event.answer("❌", alert=True)
        return
    
    user_id = int(event.data_match.group(1).decode())
    
    if user_id in pending_payments:
        del pending_payments[user_id]
    
    await event.edit("❌ **Paiement rejeté**")
    
    try:
        await client.send_message(user_id, "❌ Paiement refusé. Contactez @Kouamappoloak")
    except:
        pass

@client.on(events.MessageEdited)
async def handle_edit(event):
    if event.is_group or event.is_channel:
        if event.chat_id == get_source_channel_id():
            await process_source_message(event, is_edit=True)

# ============================================================
# SERVEUR WEB
# ============================================================

async def web_index(request):
    html = f"""<!DOCTYPE html>
<html>
<head><title>Bot Baccarat</title>
<style>
body {{ font-family: Arial; background: linear-gradient(135deg, #1e3c72, #2a5298); color: white; text-align: center; padding: 50px; }}
.status {{ background: rgba(255,255,255,0.1); padding: 20px; border-radius: 10px; display: inline-block; margin: 10px; }}
.number {{ font-size: 2em; color: #ffd700; }}
</style></head>
<body>
<h1>🎰 Bot Baccarat</h1>
<div class="status"><div>Jeu Actuel</div><div class="number">#{current_game_number}</div></div>
<div class="="status"><div>Utilisateurs</div><div class="number">{len(users_data)}</div></div>
<div class="status"><div>Vérification</div><div class="number">{verification_state['predicted_number'] or 'Aucune'}</div></div>
<div class="status"><div>Prédictions</div><div class="number">{'🟢 ON' if predictions_enabled else '🔴 OFF'}</div></div>
<p>⏱️ Essai: {trial_config['duration_minutes']}min | Pause: {pause_config['predictions_count']}/5</p>
</body></html>"""
    return web.Response(text=html, content_type='text/html')

async def start_web():
    app = web.Application()
    app.router.add_get('/', web_index)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', PORT).start()

# ============================================================
# DÉMARRAGE
# ============================================================

async def main():
    load_all_configs()
    await start_web()
    await client.start(bot_token=BOT_TOKEN)
    
    logger.info(f"🚀 Bot démarré - Admin: {ADMIN_ID}")
    logger.info(f"📺 Source: {get_source_channel_id()} | Prédiction: {get_prediction_channel_id()}")
    
    await client.run_until_disconnected()

if __name__ == '__main__':
    asyncio.run(main())
