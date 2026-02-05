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
PAYMENT_LINK_24H = "https://my.moneyfusion.net/6977f7502181d4ebf722398d"
USERS_FILE = "users_data.json"
PAUSE_CONFIG_FILE = "pause_config.json"
VIP_CONFIG_FILE = "vip_config.json"
CHANNELS_CONFIG_FILE = "channels_config.json"
TRIAL_CONFIG_FILE = "trial_config.json"

# Configuration pour l'administrateur
ADMIN_NAME = "Sossou Kouamé"
ADMIN_TITLE = "Administrateur et développeur de ce Bot"

# Configuration par défaut des canaux
DEFAULT_SOURCE_CHANNEL_ID = -1002682552255
DEFAULT_PREDICTION_CHANNEL_ID = -1003502536129
DEFAULT_VIP_CHANNEL_ID = -1003502536129
DEFAULT_VIP_CHANNEL_LINK = "https://t.me/+3pHxyUtjt34zMzg0"

# --- Configuration et Initialisation ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Vérifications
if not API_ID or API_ID == 0:
    logger.error("API_ID manquant")
    exit(1)
if not API_HASH:
    logger.error("API_HASH manquant")
    exit(1)
if not BOT_TOKEN:
    logger.error("BOT_TOKEN manquant")
    exit(1)

# Initialisation du client
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

vip_config = {
    'channel_id': DEFAULT_VIP_CHANNEL_ID,
    'channel_link': DEFAULT_VIP_CHANNEL_LINK
}

pending_predictions = {}
queued_predictions = {}
processed_messages = set()
current_game_number = 0
last_source_game_number = 0

current_prediction_target = None
last_predicted_number = None

# 🔴 NOUVEAU: Suivi des vérifications en cours
verification_state = {
    'predicted_number': None,      # Numéro prédit (ex: 24)
    'predicted_suit': None,        # Costume prédit (ex: ♣)
    'current_check': 0,            # 0=N, 1=N+1, 2=N+2, 3=N+3
    'message_id': None,            # ID message prédiction
    'channel_id': None,            # Canal prédiction
    'status': None                 # pending, ✅0️⃣, ✅1️⃣, ✅2️⃣, ✅3️⃣, ❌
}

SUIT_CYCLE = ['♥', '♦', '♣', '♠', '♦', '♥', '♠', '♣']

already_predicted_games = set()
stats_bilan = {
    'total': 0,
    'wins': 0,
    'losses': 0,
    'win_details': {'✅0️⃣': 0, '✅1️⃣': 0, '✅2️⃣': 0, '✅3️⃣': 0},
    'loss_details': {'❌': 0}
}

users_data = {}
user_conversation_state = {}
pending_payments = {}
admin_setting_time = {}
admin_message_state = {}

predictions_enabled = True

# 🔴 NOUVEAU: Stockage des messages en attente de finalisation
pending_finalization = {}

# ============================================================
# CONFIGURATION DE L'ESSAI
# ============================================================

def load_trial_config():
    global trial_config
    try:
        if os.path.exists(TRIAL_CONFIG_FILE):
            with open(TRIAL_CONFIG_FILE, 'r', encoding='utf-8') as f:
                saved = json.load(f)
                trial_config.update(saved)
    except Exception as e:
        logger.error(f"Erreur chargement trial_config: {e}")

def save_trial_config():
    try:
        with open(TRIAL_CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(trial_config, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Erreur sauvegarde trial_config: {e}")

def get_trial_duration():
    return trial_config.get('duration_minutes', DEFAULT_TRIAL_DURATION)

def set_trial_duration(minutes):
    trial_config['duration_minutes'] = minutes
    save_trial_config()

# ============================================================
# GESTION DES NUMÉROS PAIRS VALIDES
# ============================================================

def get_valid_even_numbers():
    valid_numbers = []
    for num in range(6, 1437):
        if num % 2 == 0 and num % 10 != 0:
            valid_numbers.append(num)
    return valid_numbers

VALID_EVEN_NUMBERS = get_valid_even_numbers()

def get_suit_for_number(number):
    if number not in VALID_EVEN_NUMBERS:
        return None
    idx = VALID_EVEN_NUMBERS.index(number) % len(SUIT_CYCLE)
    return SUIT_CYCLE[idx]

def get_next_prediction_number(after_number):
    for num in VALID_EVEN_NUMBERS:
        if num > after_number:
            return num
    return None

def is_valid_prediction_number(number):
    return number in VALID_EVEN_NUMBERS

def is_trigger_number(number):
    """Vérifie si c'est un déclencheur (impair à 1 part d'un pair valide)"""
    if number % 2 == 0:  # Si c'est pair, c'est pas un déclencheur
        return False
    next_num = number + 1
    return next_num in VALID_EVEN_NUMBERS

def get_trigger_target(number):
    """Retourne le pair valide qui suit ce déclencheur"""
    if not is_trigger_number(number):
        return None
    return number + 1

# ============================================================
# GESTION DES PAUSES
# ============================================================

def load_pause_config():
    global pause_config
    try:
        if os.path.exists(PAUSE_CONFIG_FILE):
            with open(PAUSE_CONFIG_FILE, 'r', encoding='utf-8') as f:
                saved = json.load(f)
                pause_config.update(saved)
    except Exception as e:
        logger.error(f"Erreur chargement pause_config: {e}")

def save_pause_config():
    try:
        with open(PAUSE_CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(pause_config, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Erreur sauvegarde pause_config: {e}")

def get_next_pause_duration():
    cycle = pause_config['cycle']
    idx = pause_config['current_index'] % len(cycle)
    return cycle[idx]

def increment_pause_index():
    pause_config['current_index'] += 1
    save_pause_config()

def should_pause():
    return pause_config['predictions_count'] >= 5

def start_pause():
    duration = get_next_pause_duration()
    pause_config['is_paused'] = True
    pause_config['pause_end_time'] = (datetime.now() + timedelta(seconds=duration)).isoformat()
    pause_config['predictions_count'] = 0
    increment_pause_index()
    save_pause_config()
    logger.info(f"⏸️ Pause démarrée pour {duration} secondes")
    return duration

def is_currently_paused():
    if not pause_config['is_paused']:
        return False
    try:
        end_time = datetime.fromisoformat(pause_config['pause_end_time'])
        if datetime.now() < end_time:
            return True
        else:
            pause_config['is_paused'] = False
            pause_config['just_resumed'] = True
            save_pause_config()
            return False
    except:
        pause_config['is_paused'] = False
        return False

def get_remaining_pause_time():
    if not is_currently_paused():
        return 0
    try:
        end_time = datetime.fromisoformat(pause_config['pause_end_time'])
        remaining = (end_time - datetime.now()).total_seconds()
        return max(0, int(remaining))
    except:
        return 0

def record_prediction():
    pause_config['predictions_count'] += 1
    pause_config['last_prediction_time'] = datetime.now().isoformat()
    save_pause_config()

def reset_pause_counter():
    pause_config['predictions_count'] = 0
    save_pause_config()

# ============================================================
# GESTION DES CANAUX
# ============================================================

def load_channels_config():
    global channels_config
    try:
        if os.path.exists(CHANNELS_CONFIG_FILE):
            with open(CHANNELS_CONFIG_FILE, 'r', encoding='utf-8') as f:
                saved_config = json.load(f)
                channels_config.update(saved_config)
                logger.info(f"Config canaux chargée")
        else:
            save_channels_config()
    except Exception as e:
        logger.error(f"Erreur chargement channels_config: {e}")

def save_channels_config():
    try:
        with open(CHANNELS_CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(channels_config, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Erreur sauvegarde channels_config: {e}")

def get_source_channel_id():
    return channels_config.get('source_channel_id', DEFAULT_SOURCE_CHANNEL_ID)

def get_prediction_channel_id():
    return channels_config.get('prediction_channel_id', DEFAULT_PREDICTION_CHANNEL_ID)

def get_vip_channel_id():
    return channels_config.get('vip_channel_id', DEFAULT_VIP_CHANNEL_ID)

def get_vip_channel_link():
    return channels_config.get('vip_channel_link', DEFAULT_VIP_CHANNEL_LINK)

def set_source_channel(channel_id: int):
    channels_config['source_channel_id'] = channel_id
    save_channels_config()
    logger.info(f"Canal source mis à jour: {channel_id}")

def set_prediction_channel(channel_id: int):
    channels_config['prediction_channel_id'] = channel_id
    save_channels_config()
    logger.info(f"Canal prédiction mis à jour: {channel_id}")

def set_vip_channel(channel_id: int, channel_link: str):
    channels_config['vip_channel_id'] = channel_id
    channels_config['vip_channel_link'] = channel_link
    vip_config['channel_id'] = channel_id
    vip_config['channel_link'] = channel_link
    save_channels_config()
    save_vip_config()
    logger.info(f"Canal VIP mis à jour: ID={channel_id}")

def reset_channels_config():
    global channels_config
    channels_config = {
        'source_channel_id': DEFAULT_SOURCE_CHANNEL_ID,
        'prediction_channel_id': DEFAULT_PREDICTION_CHANNEL_ID,
        'vip_channel_id': DEFAULT_VIP_CHANNEL_ID,
        'vip_channel_link': DEFAULT_VIP_CHANNEL_LINK
    }
    save_channels_config()
    logger.info("Configuration des canaux réinitialisée")

# ============================================================
# GESTION VIP CONFIG
# ============================================================

def load_vip_config():
    global vip_config
    try:
        if os.path.exists(VIP_CONFIG_FILE):
            with open(VIP_CONFIG_FILE, 'r', encoding='utf-8') as f:
                saved_config = json.load(f)
                vip_config.update(saved_config)
    except Exception as e:
        logger.error(f"Erreur chargement vip_config: {e}")

def save_vip_config():
    try:
        with open(VIP_CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(vip_config, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Erreur sauvegarde vip_config: {e}")

# ============================================================
# GESTION DES UTILISATEURS
# ============================================================

def load_users_data():
    global users_data
    try:
        if os.path.exists(USERS_FILE):
            with open(USERS_FILE, 'r', encoding='utf-8') as f:
                users_data = json.load(f)
    except Exception as e:
        logger.error(f"Erreur chargement users_data: {e}")
        users_data = {}

def save_users_data():
    try:
        with open(USERS_FILE, 'w', encoding='utf-8') as f:
            json.dump(users_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Erreur sauvegarde users_data: {e}")

def get_user(user_id: int) -> dict:
    user_id_str = str(user_id)
    if user_id_str not in users_data:
        users_data[user_id_str] = {
            'registered': False,
            'nom': None,
            'prenom': None,
            'pays': None,
            'trial_started': None,
            'trial_used': False,
            'trial_joined_at': None,
            'subscription_end': None,
            'subscription_type': None,
            'pending_payment': False,
            'awaiting_screenshot': False,
            'awaiting_amount': False,
            'vip_expires_at': None,
            'vip_duration_minutes': None,
            'vip_joined_at': None,
            'is_in_channel': False,
            'total_time_added': 0
        }
        save_users_data()
    return users_data[user_id_str]

def update_user(user_id: int, data: dict):
    user_id_str = str(user_id)
    if user_id_str not in users_data:
        get_user(user_id)
    users_data[user_id_str].update(data)
    save_users_data()

def is_user_subscribed(user_id: int) -> bool:
    if user_id == ADMIN_ID:
        return True
    user = get_user(user_id)
    if not user.get('subscription_end'):
        return False
    try:
        sub_end = datetime.fromisoformat(user['subscription_end'])
        return datetime.now() < sub_end
    except:
        return False

def is_trial_active(user_id: int) -> bool:
    user = get_user(user_id)
    if user.get('trial_used') or not user.get('trial_joined_at'):
        return False
    try:
        trial_start = datetime.fromisoformat(user['trial_joined_at'])
        trial_duration = get_trial_duration()
        trial_end = trial_start + timedelta(minutes=trial_duration)
        return datetime.now() < trial_end
    except:
        return False

def get_trial_time_remaining(user_id: int) -> int:
    user = get_user(user_id)
    if not user.get('trial_joined_at'):
        return 0
    try:
        trial_start = datetime.fromisoformat(user['trial_joined_at'])
        trial_duration = get_trial_duration()
        trial_end = trial_start + timedelta(minutes=trial_duration)
        remaining = (trial_end - datetime.now()).total_seconds()
        return max(0, int(remaining))
    except:
        return 0

def can_receive_predictions(user_id: int) -> bool:
    user = get_user(user_id)
    if not user.get('registered'):
        return False
    return is_user_subscribed(user_id) or is_trial_active(user_id)

def get_user_status(user_id: int) -> str:
    if is_user_subscribed(user_id):
        return "✅ Abonné"
    elif is_trial_active(user_id):
        return "🎁 Essai actif"
    elif get_user(user_id).get('trial_used'):
        return "⏰ Essai terminé"
    else:
        return "❌ Non inscrit"

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
        if minutes > 0 or hours > 0:
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
            hours = float(input_str[:-1])
            return int(hours * 60)
        except:
            return 0
    if input_str.endswith('m'):
        try:
            return int(input_str[:-1])
        except:
            return 0
    return 0

# ============================================================
# GESTION DU CANAL VIP - ESSAI ET ABONNEMENT
# ============================================================

async def add_user_to_trial(user_id: int):
    try:
        trial_duration = get_trial_duration()
        now = datetime.now()
        expires_at = now + timedelta(minutes=trial_duration)

        update_user(user_id, {
            'trial_joined_at': now.isoformat(),
            'is_in_channel': True,
            'trial_used': False
        })

        vip_link = get_vip_channel_link()
        time_str = format_time_remaining(expires_at.isoformat())

        link_msg = await client.send_message(user_id, f"""🎉 **BIENVENUE EN PÉRIODE D'ESSAI!** 🎉

✅ Vous avez {trial_duration} minutes d'accès GRATUIT au canal VIP!
⏳ Temps restant: {time_str}

🔗 **Lien du canal:** {vip_link}

⚠️ **IMPORTANT:** 
• Ce lien disparaîtra dans 10 secondes!
• Rejoignez IMMÉDIATEMENT!
• Après {trial_duration} min, vous serez retiré automatiquement

🚀 **Bonne chance avec les prédictions!**""")

        asyncio.create_task(delete_message_after_delay(user_id, link_msg.id, 10))

        user = get_user(user_id)
        await client.send_message(ADMIN_ID, f"""🆕 **NOUVEL UTILISATEUR EN ESSAI**

👤 **Nom:** {user.get('prenom', '')} {user.get('nom', '')}
🆔 **ID:** `{user_id}`
📍 **Pays:** {user.get('pays', 'N/A')}
⏳ **Durée:** {trial_duration} minutes
📅 **Expire le:** {expires_at.strftime('%d/%m/%Y %H:%M:%S')}

🔗 Lien envoyé (suppression dans 10s)""")

        asyncio.create_task(auto_kick_trial_user(user_id, trial_duration * 60))

        logger.info(f"Utilisateur {user_id} ajouté en essai pour {trial_duration} minutes")
        return True

    except Exception as e:
        logger.error(f"Erreur ajout utilisateur {user_id} en essai: {e}")
        return False

async def auto_kick_trial_user(user_id: int, delay_seconds: int):
    await asyncio.sleep(delay_seconds)

    try:
        user = get_user(user_id)
        
        if is_user_subscribed(user_id):
            logger.info(f"Utilisateur {user_id} a souscrit, annulation de l'expulsion d'essai")
            return
        
        if not user.get('trial_joined_at'):
            return

        vip_channel_id = get_vip_channel_id()
        
        try:
            entity = await client.get_input_entity(vip_channel_id)
            await client.kick_participant(entity, user_id)
            await client(EditBannedRequest(
                channel=entity,
                participant=user_id,
                banned_rights=ChatBannedRights(until_date=None, view_messages=False)
            ))
        except Exception as e:
            logger.error(f"Erreur expulsion essai {user_id}: {e}")

        update_user(user_id, {
            'trial_used': True,
            'is_in_channel': False,
            'trial_joined_at': None
        })

        buttons = [
            [Button.url("💳 Payer maintenant", PAYMENT_LINK)]
        ]
        
        try:
            await client.send_message(user_id, f"""⏰ **VOTRE ESSAI EST TERMINÉ**

Vous avez été retiré du canal VIP après {get_trial_duration()} minutes.

💳 **Pour réintégrer le canal, payez maintenant:**

👇 Cliquez ci-dessous:""", buttons=buttons)
        except:
            pass

        await client.send_message(ADMIN_ID, f"""🚫 **ESSAI TERMINÉ - UTILISATEUR RETIRÉ**

🆔 **ID:** `{user_id}`
👤 **Nom:** {user.get('prenom', '')} {user.get('nom', '')}
📍 **Pays:** {user.get('pays', 'N/A')}

L'utilisateur a été expulsé après la période d'essai.
Message de paiement envoyé.""")

        logger.info(f"Utilisateur {user_id} expulsé après essai")

    except Exception as e:
        logger.error(f"Erreur expulsion essai utilisateur {user_id}: {e}")

async def add_user_to_vip(user_id: int, duration_minutes: int):
    try:
        now = datetime.now()
        expires_at = now + timedelta(minutes=duration_minutes)

        update_user(user_id, {
            'vip_joined_at': now.isoformat(),
            'vip_expires_at': expires_at.isoformat(),
            'vip_duration_minutes': duration_minutes,
            'is_in_channel': True,
            'subscription_end': expires_at.isoformat(),
            'total_time_added': user.get('total_time_added', 0) + duration_minutes
        })

        time_str = format_time_remaining(expires_at.isoformat())
        vip_link = get_vip_channel_link()

        link_msg = await client.send_message(user_id, f"""🎉 **FÉLICITATIONS! VOTRE ABONNEMENT EST ACTIVÉ!** 🎉

✅ Vous avez maintenant accès au canal VIP!
⏳ Temps restant: {time_str}

🔗 **Lien du canal:** {vip_link}

⚠️ **Important:** 
• Ce lien disparaîtra dans 2 minutes pour des raisons de sécurité
• Veillez rejoindre rapidement!
• Renouvelez avant expiration

🚀 **Bonne chance avec les prédictions!**""")

        asyncio.create_task(delete_message_after_delay(user_id, link_msg.id, 120))

        user = get_user(user_id)
        await client.send_message(ADMIN_ID, f"""✅ **UTILISATEUR ABONNÉ AU CANAL VIP**

👤 **Nom:** {user.get('prenom', '')} {user.get('nom', '')}
🆔 **ID:** `{user_id}`
⏳ **Temps restant:** {time_str}
📅 **Expire le:** {expires_at.strftime('%d/%m/%Y %H:%M:%S')}
⏱️ **Durée totale ajoutée:** {user.get('total_time_added', 0) + duration_minutes} min

🔗 Lien envoyé (sera supprimé dans 2 min)""")

        asyncio.create_task(auto_kick_user(user_id, duration_minutes * 60))

        logger.info(f"Utilisateur {user_id} ajouté au canal VIP pour {duration_minutes} minutes")
        return True

    except Exception as e:
        logger.error(f"Erreur ajout utilisateur {user_id} au VIP: {e}")
        return False

async def delete_message_after_delay(chat_id: int, message_id: int, delay_seconds: int):
    await asyncio.sleep(delay_seconds)
    try:
        await client.delete_messages(chat_id, [message_id])
        logger.info(f"Message {message_id} supprimé après {delay_seconds}s")
    except Exception as e:
        logger.error(f"Erreur suppression message {message_id}: {e}")

async def auto_kick_user(user_id: int, delay_seconds: int):
    await asyncio.sleep(delay_seconds)

    try:
        user = get_user(user_id)
        if not user.get('vip_expires_at'):
            return

        vip_channel_id = get_vip_channel_id()
        
        try:
            entity = await client.get_input_entity(vip_channel_id)
        except Exception as e:
            logger.error(f"Impossible de trouver l'entité du canal {vip_channel_id}: {e}")
            await client.get_dialogs()
            entity = await client.get_input_entity(vip_channel_id)

        await client.kick_participant(entity, user_id)

        await client(EditBannedRequest(
            channel=entity,
            participant=user_id,
            banned_rights=ChatBannedRights(until_date=None, view_messages=False)
        ))

        update_user(user_id, {
            'vip_expires_at': None,
            'vip_duration_minutes': None,
            'is_in_channel': False,
            'subscription_end': None
        })

        try:
            buttons = [
                [Button.url("💳 Renouveler", PAYMENT_LINK)]
            ]
            await client.send_message(user_id, """❌ **VOTRE ABONNEMENT EST TERMINÉ**

Vous avez été retiré du canal VIP.

💳 Pour réintégrer le canal, payez maintenant:""", buttons=buttons)
        except:
            pass

        await client.send_message(ADMIN_ID, f"""🚫 **ABONNEMENT TERMINÉ - UTILISATEUR RETIRÉ**

🆔 **ID:** `{user_id}`
👤 **Nom:** {user.get('prenom', '')} {user.get('nom', '')}

L'utilisateur a été expulsé du canal VIP.""")

        logger.info(f"Utilisateur {user_id} expulsé du canal VIP (abonnement terminé)")

    except Exception as e:
        logger.error(f"Erreur expulsion utilisateur {user_id}: {e}")

# ============================================================
# SYSTÈME DE PRÉDICTION - CORRIGÉ
# ============================================================

async def send_prediction(target_game, predicted_suit, base_game):
    global verification_state, last_predicted_number
    
    if not predictions_enabled:
        logger.info("Prédictions désactivées, envoi annulé.")
        return False
    
    # Vérifier si une prédiction est déjà en cours
    if verification_state['predicted_number'] is not None:
        logger.warning(f"Prédiction déjà en cours (#{verification_state['predicted_number']}), nouvelle annulée")
        return False
    
    try:
        prediction_channel_id = get_prediction_channel_id()
        entity = await client.get_input_entity(prediction_channel_id)
        
        prediction_msg = f"""🎰 **PRÉDICTION #{target_game}**
🎯 Couleur: {SUIT_DISPLAY.get(predicted_suit, predicted_suit)}
⏳ Statut: EN ATTENTE..."""
        
        sent_msg = await client.send_message(entity, prediction_msg)
        
        # Initialiser l'état de vérification
        verification_state = {
            'predicted_number': target_game,
            'predicted_suit': predicted_suit,
            'current_check': 0,  # 0 = N, 1 = N+1, 2 = N+2, 3 = N+3
            'message_id': sent_msg.id,
            'channel_id': prediction_channel_id,
            'status': 'pending'
        }
        
        last_predicted_number = target_game
        record_prediction()
        
        logger.info(f"✅ Prédiction envoyée: #{target_game} -> {predicted_suit}")
        logger.info(f"🔍 Vérification démarrée: attendre #{target_game} (check 0/4)")
        return True
        
    except Exception as e:
        logger.error(f"❌ Erreur envoi prédiction: {e}")
        return False

async def update_prediction_status(status):
    """Met à jour le statut de la prédiction dans le canal"""
    global verification_state, stats_bilan
    
    if not verification_state['predicted_number']:
        return False
    
    try:
        channel_id = verification_state['channel_id']
        message_id = verification_state['message_id']
        predicted_num = verification_state['predicted_number']
        suit = verification_state['predicted_suit']
        
        # Déterminer le texte du statut
        if status == "❌":
            status_text = "❌ PERDU"
        elif status == "✅0️⃣":
            status_text = "✅0️⃣ GAGNÉ IMMÉDIAT!"
        elif status == "✅1️⃣":
            status_text = "✅1️⃣ GAGNÉ AU 2ÈME!"
        elif status == "✅2️⃣":
            status_text = "✅2️⃣ GAGNÉ AU 3ÈME!"
        elif status == "✅3️⃣":
            status_text = "✅3️⃣ GAGNÉ AU 4ÈME!"
        else:
            status_text = status
        
        updated_msg = f"""🎰 **PRÉDICTION #{predicted_num}**
🎯 Couleur: {SUIT_DISPLAY.get(suit, suit)}
📊 Statut: {status_text}"""
        
        await client.edit_message(channel_id, message_id, updated_msg)
        
        # Mettre à jour les stats
        if status in ['✅0️⃣', '✅1️⃣', '✅2️⃣', '✅3️⃣']:
            stats_bilan['total'] += 1
            stats_bilan['wins'] += 1
            stats_bilan['win_details'][status] = stats_bilan['win_details'].get(status, 0) + 1
        elif status == '❌':
            stats_bilan['total'] += 1
            stats_bilan['losses'] += 1
            stats_bilan['loss_details']['❌'] = stats_bilan['loss_details'].get('❌', 0) + 1
        
        # Réinitialiser l'état de vérification
        logger.info(f"🏁 Prédiction #{predicted_num} terminée avec statut: {status}")
        verification_state = {
            'predicted_number': None,
            'predicted_suit': None,
            'current_check': 0,
            'message_id': None,
            'channel_id': None,
            'status': None
        }
        
        return True
        
    except Exception as e:
        logger.error(f"Erreur mise à jour prédiction: {e}")
        return False

# ============================================================
# FONCTIONS D'ANALYSE DES MESSAGES
# ============================================================

def extract_game_number(message):
    # Chercher #N suivi de chiffres
    match = re.search(r"#N\s*(\d+)", message, re.IGNORECASE)
    if match:
        return int(match.group(1))
    # Chercher # suivi de chiffres au début
    match = re.search(r"^#(\d+)", message)
    if match:
        return int(match.group(1))
    # Chercher N suivi de chiffres
    match = re.search(r"N\s*(\d+)", message, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None

def extract_first_group_suits(message_text):
    """Extrait les costumes du PREMIER groupe de parenthèses"""
    matches = re.findall(r"\(([^)]+)\)", message_text)
    if not matches:
        return []
    
    first_group = matches[0]
    suits = []
    
    # Normaliser les costumes
    normalized = first_group.replace('❤️', '♥').replace('❤', '♥').replace('♥️', '♥')
    normalized = normalized.replace('♠️', '♠').replace('♦️', '♦').replace('♣️', '♣')
    
    for suit in ['♥', '♠', '♦', '♣']:
        if suit in normalized:
            suits.append(suit)
    
    return suits

def is_message_finalized(message_text):
    """Vérifie si le message est finalisé (✅ ou 🔰 présent)"""
    return '✅' in message_text or '🔰' in message_text

def is_message_being_edited(message_text):
    """Vérifie si le message est en cours d'édition (⏰ au début)"""
    return message_text.strip().startswith('⏰')

# ============================================================
# SYSTÈME DE VÉRIFICATION - NOUVEAU
# ============================================================

async def process_verification(game_number, message_text):
    """
    Gère la vérification séquentielle de la prédiction
    """
    global verification_state
    
    if verification_state['predicted_number'] is None:
        return  # Pas de prédiction en cours
    
    predicted_num = verification_state['predicted_number']
    predicted_suit = verification_state['predicted_suit']
    current_check = verification_state['current_check']
    
    # Calculer quel numéro on doit vérifier maintenant
    expected_number = predicted_num + current_check
    
    # Vérifier si c'est le bon numéro
    if game_number != expected_number:
        logger.info(f"⏳ Attente #{expected_number}, reçu #{game_number} - ignoré")
        return
    
    # Vérifier le costume dans le premier groupe
    suits = extract_first_group_suits(message_text)
    logger.info(f"🔍 Vérification #{game_number} (check {current_check}/3): costumes trouvés {suits}, attendu {predicted_suit}")
    
    if predicted_suit in suits:
        # Costume trouvé !
        status = f"✅{current_check}️⃣"
        await update_prediction_status(status)
        return
    
    # Costume pas trouvé, passer au suivant
    if current_check < 3:
        # Passer au check suivant (N+1, N+2, N+3)
        verification_state['current_check'] += 1
        next_num = predicted_num + verification_state['current_check']
        logger.info(f"❌ Pas trouvé sur #{game_number}, prochain check: #{next_num}")
    else:
        # Dernier check (N+3) échoué
        logger.info(f"❌ Perdu après 4 vérifications (jusqu'à #{game_number})")
        await update_prediction_status("❌")

# ============================================================
# TRAITEMENT DES MESSAGES SOURCE - CORRIGÉ
# ============================================================

async def process_source_message(event, is_edit=False):
    global current_game_number, last_source_game_number, pending_finalization
    
    try:
        message_text = event.message.message
        msg_type = "ÉDITÉ" if is_edit else "NOUVEAU"
        
        # Extraire le numéro
        game_number = extract_game_number(message_text)
        
        if game_number is None:
            logger.debug(f"Message {msg_type} sans numéro détecté")
            return
        
        logger.info(f"📩 Message {msg_type} reçu: #{game_number} - {message_text[:80]}...")
        
        # Si message en édition (⏰), stocker et attendre
        if is_message_being_edited(message_text):
            logger.info(f"⏳ Message #{game_number} en édition, mise en attente...")
            pending_finalization[game_number] = message_text
            return
        
        # Si message finalisé (✅ ou 🔰)
        if is_message_finalized(message_text):
            # Retirer des pending si présent
            if game_number in pending_finalization:
                del pending_finalization[game_number]
            
            current_game_number = game_number
            last_source_game_number = game_number
            
            logger.info(f"✅ Message #{game_number} finalisé détecté")
            
            # 🔴 VÉRIFICATION: Si on a une prédiction en cours, vérifier ce numéro
            if verification_state['predicted_number'] is not None:
                await process_verification(game_number, message_text)
            
            # 🔴 LANCEMENT AUTO: Vérifier si c'est un déclencheur et pas de prédiction active
            if verification_state['predicted_number'] is None and not is_currently_paused():
                await check_and_launch_prediction(game_number)
        
        # Si message ni en édition ni finalisé, ignorer pour la vérification
        # mais vérifier quand même pour le lancement auto
        elif not is_message_being_edited(message_text):
            # Message normal sans ✅/🔰 (rare mais possible)
            current_game_number = game_number
            last_source_game_number = game_number
            
            # Vérifier quand même pour lancement auto
            if verification_state['predicted_number'] is None and not is_currently_paused():
                await check_and_launch_prediction(game_number)
        
    except Exception as e:
        logger.error(f"❌ Erreur process_source_message: {e}")
        import traceback
        logger.error(traceback.format_exc())

async def check_and_launch_prediction(game_number):
    """
    Vérifie si on doit lancer une prédiction automatique
    """
    global pause_config
    
    # Vérifier si c'est un déclencheur (impair à 1 part)
    if not is_trigger_number(game_number):
        logger.info(f"#{game_number} n'est pas un déclencheur, attente...")
        return
    
    # Obtenir le pair valide cible
    target_num = get_trigger_target(game_number)
    if not target_num:
        logger.warning(f"Impossible de trouver le target pour déclencheur #{game_number}")
        return
    
    # Vérifier si déjà prédit
    if target_num in already_predicted_games:
        logger.info(f"#{target_num} déjà prédit, ignoré")
        return
    
    # Vérifier pause
    if should_pause():
        duration = start_pause()
        minutes = duration // 60
        logger.info(f"⏸️ Début pause automatique ({minutes} minutes)")
        try:
            await client.send_message(
                get_prediction_channel_id(),
                f"⏸️ **PAUSE**\nProchaine prédiction dans {minutes} minutes..."
            )
        except Exception as e:
            logger.error(f"Erreur envoi message pause: {e}")
        return
    
    # Gestion reprise après pause
    if pause_config.get('just_resumed'):
        pause_config['just_resumed'] = False
        save_pause_config()
        # Après pause, on attend un nouveau déclencheur (déjà vérifié ci-dessus)
        logger.info(f"🔄 Reprise après pause, déclencheur #{game_number} détecté")
    
    # Lancer la prédiction
    suit = get_suit_for_number(target_num)
    if suit:
        logger.info(f"🔮 Déclencheur #{game_number} détecté → Prédiction #{target_num} -> {suit}")
        success = await send_prediction(target_num, suit, game_number)
        if success:
            already_predicted_games.add(target_num)

# ============================================================
# GESTION DES MESSAGES ET COMMANDES
# ============================================================

@client.on(events.NewMessage)
async def handle_new_message(event):
    if event.is_group or event.is_channel:
        if event.chat_id == get_source_channel_id():
            await process_source_message(event, is_edit=False)
        return

    # Gestion messages privés (inchangé)
    if event.message.message and event.message.message.startswith('/'):
        return

    user_id = event.sender_id
    user = get_user(user_id)

    # ... (reste du code de gestion des messages privés inchangé)
    # Gestion inscription, paiement, etc.
    
    if user_id == ADMIN_ID and user_id in admin_setting_time:
        state = admin_setting_time[user_id]
        if state['step'] == 'awaiting_duration':
            duration_input = event.message.message.strip()
            target_user_id = state['target_user_id']
            
            duration_minutes = parse_duration(duration_input)
            
            if duration_minutes is None or duration_minutes == 0:
                await event.respond("❌ Format invalide. Réessayez (ex: 120, 2h, 30m).")
                return
                
            await add_user_to_vip(target_user_id, duration_minutes)
            del admin_setting_time[user_id]
            
            if target_user_id in pending_payments:
                del pending_payments[target_user_id]
                
            return

    if user_id == ADMIN_ID and user_id in admin_message_state:
        state = admin_message_state[user_id]
        if state['step'] == 'awaiting_message':
            target_user_id = state['target_user_id']
            msg_text = event.message.message.strip()
            
            current_time = datetime.now().strftime('%H:%M')
            full_message = f"""📬 **MESSAGE DE L'ADMINISTRATEUR**
            
{msg_text}

---
⏰ Envoyé à {current_time}"""

            try:
                await client.send_message(target_user_id, full_message)
                await event.respond(f"✅ Message envoyé à {target_user_id}!")
            except Exception as e:
                await event.respond(f"❌ Erreur: {e}")

            del admin_message_state[user_id]
            return

    if user_id in user_conversation_state:
        state = user_conversation_state[user_id]
        message_text = event.message.message.strip()

        if state == 'awaiting_nom':
            if not message_text:
                await event.respond("❌ Veuillez entrer un nom valide.")
                return

            update_user(user_id, {'nom': message_text})
            user_conversation_state[user_id] = 'awaiting_prenom'
            await event.respond(f"""✅ **Nom: {message_text}**

📝 **Étape 2/3: Votre prénom?**""")
            return

        elif state == 'awaiting_prenom':
            if not message_text:
                await event.respond("❌ Veuillez entrer un prénom valide.")
                return

            update_user(user_id, {'prenom': message_text})
            user_conversation_state[user_id] = 'awaiting_pays'
            await event.respond(f"""✅ **Enchanté {message_text}!**

🌍 **Étape 3/3: Votre pays?**""")
            return

        elif state == 'awaiting_pays':
            if not message_text:
                await event.respond("❌ Veuillez entrer un pays valide.")
                return

            update_user(user_id, {
                'pays': message_text,
                'registered': True,
                'trial_started': datetime.now().isoformat(),
                'trial_used': False
            })
            del user_conversation_state[user_id]

            await client.send_message(ADMIN_ID, f"""🆕 **NOUVELLE INSCRIPTION**

👤 **Nom:** {message_text} {user.get('nom', '')}
🆔 **ID:** `{user_id}`
📍 **Pays:** {message_text}

L'utilisateur va recevoir le lien d'essai de {get_trial_duration()} min.""")

            await add_user_to_trial(user_id)

            success_msg = f"""🎉 **INSCRIPTION RÉUSSIE!** 🎉

✅ Votre compte est ACTIVÉ!
⏰ **{get_trial_duration()} MINUTES D'ESSAI GRATUIT**

🔗 Le lien du canal VIP a été envoyé (disparaît dans 10s)!

🚀 **Comment ça marche?**
1️⃣ Rejoignez vite le canal avec le lien ci-dessus
2️⃣ Vous avez {get_trial_duration()} minutes d'accès
3️⃣ Après {get_trial_duration()} min, vous serez retiré automatiquement

⚠️ **IMPORTANT:** Restez dans ce chat pour les notifications!

🍀 **Bonne chance!**"""

            await event.respond(success_msg)
            return

    if user.get('awaiting_screenshot') and event.message.photo:
        photo = event.message.photo

        pending_payments[user_id] = {
            'photo_id': photo.id,
            'sent_at': datetime.now().isoformat(),
            'user_id': user_id
        }

        user_info = get_user(user_id)

        admin_msg = f"""🔔 **NOUVELLE DEMANDE DE PAIEMENT**

👤 **Utilisateur:** {user_info.get('prenom', '')} {user_info.get('nom', '')}
🆔 **ID:** `{user_id}`
📍 **Pays:** {user_info.get('pays', 'N/A')}
⏰ **Envoyé à:** {datetime.now().strftime('%H:%M:%S')}

✅ **Cliquez sur "Valider" pour activer**
❌ **Cliquez sur "Rejeter" pour refuser**"""

        buttons = [
            [Button.inline("✅ Valider", data=f"validate_payment_{user_id}")],
            [Button.inline("❌ Rejeter", data=f"reject_payment_{user_id}")]
        ]

        try:
            await client.send_file(ADMIN_ID, photo, caption=admin_msg, buttons=buttons)

            await event.respond("""📸 **Capture reçue!**

⏳ Votre paiement est en cours de vérification...
🚀 Vous recevrez une confirmation sous peu!""")

            update_user(user_id, {'awaiting_screenshot': False})

        except Exception as e:
            logger.error(f"Erreur transfert capture: {e}")
            await event.respond("❌ Erreur lors de l'envoi. Veuillez réessayer.")

        return

# ============================================================
# CALLBACKS VALIDATION PAIEMENT (inchangés)
# ============================================================

@client.on(events.CallbackQuery(data=re.compile(rb'validate_payment_(\d+)')))
async def handle_validate_payment(event):
    if event.sender_id != ADMIN_ID:
        await event.answer("Accès refusé", alert=True)
        return

    user_id = int(event.data_match.group(1).decode())

    if user_id not in pending_payments:
        await event.answer("Paiement déjà traité", alert=True)
        return

    admin_setting_time[ADMIN_ID] = {
        'target_user_id': user_id,
        'step': 'awaiting_duration'
    }

    user_info = get_user(user_id)

    await event.edit(f"""✅ **VALIDATION EN COURS**

👤 **Utilisateur:** {user_info.get('prenom', '')} {user_info.get('nom', '')}
🆔 **ID:** `{user_id}`

📝 **Entrez la durée d'accès:**
• `120` = 120 minutes
• `2h` = 2 heures
• `90m` = 90 minutes

⏱️ **Plage:** 2 minutes à 750 heures

✏️ **Envoyez la durée:**""")

    await event.answer("Entrez la durée", alert=False)

@client.on(events.CallbackQuery(data=re.compile(rb'reject_payment_(\d+)')))
async def handle_reject_payment(event):
    if event.sender_id != ADMIN_ID:
        await event.answer("Accès refusé", alert=True)
        return

    user_id = int(event.data_match.group(1).decode())

    if user_id in pending_payments:
        del pending_payments[user_id]

    try:
        await client.send_message(user_id, """❌ **PAIEMENT REJETÉ**

Votre paiement n'a pas été validé.

📞 Contactez @Kouamappoloak pour plus d'informations.""")
    except:
        pass

    await event.edit("❌ **Paiement rejeté**\n\nL'utilisateur a été notifié.")
    await event.answer("Rejeté", alert=False)

# ============================================================
# COMMANDES ADMIN (inchangées sauf ajouts)
# ============================================================

@client.on(events.NewMessage(pattern=r'^/setchannel(\s+.+)?$'))
async def cmd_setchannel(event):
    if event.is_group or event.is_channel: 
        return

    if event.sender_id != ADMIN_ID:
        await event.respond("❌ Commande réservée à l'administrateur.")
        return

    message_text = event.message.message.strip()
    parts = message_text.split()

    if len(parts) < 3:
        await event.respond(f"""❌ **Format invalide**

**Usage:** `/setchannel TYPE ID [LIEN]`

**Types:**
• `source` - Canal source (reçoit les jeux)
• `prediction` - Canal prédiction (envoie les prédictions)
• `vip` - Canal VIP (accès payant)

**Exemples:**
• `/setchannel source -1001234567890`
• `/setchannel prediction -1001234567890`
• `/setchannel vip -1001234567890 https://t.me/...`

**Actuellement:**
• Source: `{get_source_channel_id()}`
• Prédiction: `{get_prediction_channel_id()}`
• VIP: `{get_vip_channel_id()}`
• Lien VIP: `{get_vip_channel_link()}`""")
        return

    try:
        channel_type = parts[1].lower()
        channel_id = int(parts[2])

        if channel_type == 'source':
            set_source_channel(channel_id)
            await event.respond(f"✅ **Canal source mis à jour:**\n`{channel_id}`")

        elif channel_type == 'prediction':
            set_prediction_channel(channel_id)
            await event.respond(f"✅ **Canal prédiction mis à jour:**\n`{channel_id}`")

        elif channel_type == 'vip':
            if len(parts) < 4:
                await event.respond("❌ Pour le canal VIP, vous devez aussi fournir le lien.\nUsage: `/setchannel vip ID LIEN`")
                return
            channel_link = parts[3]
            set_vip_channel(channel_id, channel_link)
            await event.respond(f"✅ **Canal VIP mis à jour:**\nID: `{channel_id}`\nLien: {channel_link}")

        else:
            await event.respond("❌ Type invalide. Utilisez: source, prediction, ou vip")

    except ValueError:
        await event.respond("❌ ID de canal invalide.")
    except Exception as e:
        await event.respond(f"❌ Erreur: {e}")

@client.on(events.NewMessage(pattern='/channels'))
async def cmd_channels(event):
    if event.is_group or event.is_channel: 
        return

    if event.sender_id != ADMIN_ID:
        await event.respond("❌ Commande réservée à l'administrateur.")
        return

    await event.respond(f"""📺 **CONFIGURATION DES CANAUX**

**Canal Source** (reçoit les jeux):
`{get_source_channel_id()}`

**Canal Prédiction** (envoie les prédictions):
`{get_prediction_channel_id()}`

**Canal VIP** (accès payant):
ID: `{get_vip_channel_id()}`
Lien: {get_vip_channel_link()}

**Commandes:**
• `/setchannel source ID`
• `/setchannel prediction ID`
• `/setchannel vip ID LIEN`
• `/resetchannels` - Réinitialiser""")

@client.on(events.NewMessage(pattern='/resetchannels'))
async def cmd_resetchannels(event):
    if event.is_group or event.is_channel: 
        return

    if event.sender_id != ADMIN_ID:
        await event.respond("❌ Commande réservée à l'administrateur.")
        return

    reset_channels_config()
    await event.respond(f"""🔄 **CANAUX RÉINITIALISÉS**

Tous les canaux ont été réinitialisés aux valeurs par défaut:

**Valeurs par défaut:**
• Source: `{DEFAULT_SOURCE_CHANNEL_ID}`
• Prédiction: `{DEFAULT_PREDICTION_CHANNEL_ID}`
• VIP: `{DEFAULT_VIP_CHANNEL_ID}`
• Lien VIP: `{DEFAULT_VIP_CHANNEL_LINK}`""")

@client.on(events.NewMessage(pattern=r'^/settime(\s+\d+)?(\s+.+)?$'))
async def cmd_settime(event):
    if event.is_group or event.is_channel: 
        return

    if event.sender_id != ADMIN_ID:
        await event.respond("❌ Commande réservée à l'administrateur.")
        return

    message_text = event.message.message.strip()
    parts = message_text.split()

    if len(parts) < 3:
        await event.respond("""❌ **Format invalide**

**Usage:** `/settime ID_UTILISATEUR DURÉE`

**Exemples:**
• `/settime 123456789 120` → 120 minutes
• `/settime 123456789 2h` → 2 heures
• `/settime 123456789 30m` → 30 minutes

**Plage:** 2 minutes à 750 heures""")
        return

    try:
        target_user_id = int(parts[1])
        duration_input = parts[2]

        if str(target_user_id) not in users_data:
            await event.respond(f"❌ Utilisateur {target_user_id} non trouvé.")
            return

        duration_minutes = parse_duration(duration_input)

        if duration_minutes is None or duration_minutes == 0:
            await event.respond("❌ Format de durée invalide.")
            return

        if duration_minutes < 2:
            await event.respond("❌ Durée minimum: 2 minutes")
            return
        if duration_minutes > 45000:
            await event.respond("❌ Durée maximum: 750 heures")
            return

        await add_user_to_vip(target_user_id, duration_minutes)

    except ValueError:
        await event.respond("❌ ID utilisateur invalide.")
    except Exception as e:
        await event.respond(f"❌ Erreur: {e}")

@client.on(events.NewMessage(pattern='/stop'))
async def cmd_stop(event):
    if event.sender_id != ADMIN_ID: 
        return
    global predictions_enabled
    predictions_enabled = False
    await event.respond("🛑 **PRÉDICTIONS AUTOMATIQUES ARRÊTÉES**")

@client.on(events.NewMessage(pattern='/resume'))
async def cmd_resume(event):
    if event.sender_id != ADMIN_ID: 
        return
    global predictions_enabled, already_predicted_games, verification_state
    predictions_enabled = True
    already_predicted_games.clear()
    # Réinitialiser aussi l'état de vérification
    verification_state = {
        'predicted_number': None,
        'predicted_suit': None,
        'current_check': 0,
        'message_id': None,
        'channel_id': None,
        'status': None
    }
    await event.respond("🚀 **PRÉDICTIONS REDÉMARRÉES ET DÉBLOQUÉES**\n(Historique de sécurité vidé)")

@client.on(events.NewMessage(pattern=r'^/setnext (\d+) ([♥♠♦♣])$'))
async def cmd_setnext(event):
    if event.is_group or event.is_channel: 
        return

    if event.sender_id != ADMIN_ID:
        await event.respond("❌ Commande réservée à l'administrateur.")
        return

    try:
        next_num = int(event.pattern_match.group(1))
        suit = event.pattern_match.group(2)
        
        if next_num not in VALID_EVEN_NUMBERS:
            await event.respond(f"❌ {next_num} n'est pas un numéro pair valide (6-1436, sauf finissant par 0)")
            return
        
        await send_prediction(next_num, suit, last_source_game_number)
        already_predicted_games.add(next_num)
        
        await event.respond(f"""✅ **PRÉDICTION MANUELLE ENVOYÉE**

• Numéro prédit: `{next_num}`
• Costume: {SUIT_DISPLAY.get(suit, suit)}""")

    except Exception as e:
        await event.respond(f"❌ Erreur: {e}")

@client.on(events.NewMessage(pattern=r'^/pausecycle(\s+.+)?$'))
async def cmd_pausecycle(event):
    if event.is_group or event.is_channel:
        return
    
    if event.sender_id != ADMIN_ID:
        await event.respond("❌ Commande réservée à l'administrateur.")
        return
    
    message_text = event.message.message.strip()
    parts = message_text.split()
    
    if len(parts) == 1:
        current = [x//60 for x in pause_config['cycle']]
        await event.respond(f"""⏸️ **CONFIGURATION CYCLE DE PAUSE**
        
**Cycle actuel:** {current} minutes
**Index actuel:** {pause_config['current_index']}
**Prédictions avant pause:** {5 - pause_config['predictions_count']}

**Modifier:**
`/pausecycle 3,4,7` (minutes)
**Exemple:** 3min, 4min, 7min puis recommence""")
        return
    
    try:
        cycle_str = parts[1]
        new_cycle = [int(x.strip()) * 60 for x in cycle_str.split(',')]
        
        if not new_cycle or any(x <= 0 for x in new_cycle):
            await event.respond("❌ Le cycle doit contenir des nombres positifs.")
            return
        
        pause_config['cycle'] = new_cycle
        pause_config['current_index'] = 0
        save_pause_config()
        
        minutes_cycle = [x//60 for x in new_cycle]
        await event.respond(f"✅ **Cycle de pause mis à jour:** {minutes_cycle} minutes")
        
    except Exception as e:
        await event.respond(f"❌ Erreur: {e}")

@client.on(events.NewMessage(pattern='/predictinfo'))
async def cmd_predictinfo(event):
    if event.sender_id != ADMIN_ID:
        return
    
    current_cycle = [x//60 for x in pause_config['cycle']]
    
    # Info sur la vérification en cours
    verif_info = "Aucune"
    if verification_state['predicted_number']:
        next_check_num = verification_state['predicted_number'] + verification_state['current_check']
        verif_info = f"""#{verification_state['predicted_number']} ({verification_state['predicted_suit']})
Check: {verification_state['current_check']}/3 (attend #{next_check_num})"""
    
    info = f"""📊 **INFO PRÉDICTION**
    
**Numéro source actuel:** {current_game_number}
**Dernier prédit:** {last_predicted_number}
**En pause:** {'Oui' if is_currently_paused() else 'Non'}
**Temps pause restant:** {get_remaining_pause_time()}s
**Compteur avant pause:** {pause_config['predictions_count']}/5
**Index pause:** {pause_config['current_index']}
**Cycle pause:** {current_cycle} min

**Vérification en cours:**
{verif_info}
"""
    await event.respond(info)

@client.on(events.NewMessage(pattern='/forcepause'))
async def cmd_forcepause(event):
    if event.sender_id != ADMIN_ID:
        return
    
    duration = start_pause()
    minutes = duration // 60
    await event.respond(f"⏸️ **PAUSE FORCÉE**\nDurée: {minutes} minutes")

@client.on(events.NewMessage(pattern='/resetpause'))
async def cmd_resetpause(event):
    if event.sender_id != ADMIN_ID:
        return
    
    reset_pause_counter()
    pause_config['is_paused'] = False
    pause_config['just_resumed'] = False
    save_pause_config()
    await event.respond("✅ **Compteur de pause réinitialisé**")

# ============================================================
# COMMANDES ADMIN - DEBUG PRÉDICTION (NOUVEAU)
# ============================================================

@client.on(events.NewMessage(pattern='/verifstatus'))
async def cmd_verifstatus(event):
    if event.sender_id != ADMIN_ID:
        return
    
    if verification_state['predicted_number'] is None:
        await event.respond("ℹ️ Aucune vérification en cours.")
        return
    
    next_num = verification_state['predicted_number'] + verification_state['current_check']
    
    await event.respond(f"""🔍 **STATUT VÉRIFICATION**

🎯 Numéro prédit: #{verification_state['predicted_number']}
🎨 Costume: {verification_state['predicted_suit']}
🔢 Check actuel: {verification_state['current_check']}/3
⏳ Attend: #{next_num}
📊 Statut: {verification_state['status']}

💡 `/clearverif` pour forcer la réinitialisation""")

@client.on(events.NewMessage(pattern='/clearverif'))
async def cmd_clearverif(event):
    if event.sender_id != ADMIN_ID:
        return
    
    global verification_state
    
    old_num = verification_state['predicted_number']
    verification_state = {
        'predicted_number': None,
        'predicted_suit': None,
        'current_check': 0,
        'message_id': None,
        'channel_id': None,
        'status': None
    }
    
    if old_num:
        await event.respond(f"✅ Vérification #{old_num} effacée. Nouvelle prédiction possible.")
    else:
        await event.respond("ℹ️ Aucune vérification à effacer.")

# ============================================================
# COMMANDES ADMIN - GESTION DES ESSAIS (inchangées)
# ============================================================

@client.on(events.NewMessage(pattern=r'^/settrialtime(\s+\d+)?$'))
async def cmd_settrialtime(event):
    if event.sender_id != ADMIN_ID:
        return
    
    message_text = event.message.message.strip()
    parts = message_text.split()
    
    if len(parts) == 1:
        await event.respond(f"""⏱️ **DURÉE DE L'ESSAI**
        
**Actuellement:** {get_trial_duration()} minutes

**Modifier:**
`/settrialtime 15` (minutes)""")
        return
    
    try:
        minutes = int(parts[1])
        if minutes < 1 or minutes > 120:
            await event.respond("❌ La durée doit être entre 1 et 120 minutes.")
            return
        
        set_trial_duration(minutes)
        await event.respond(f"✅ **Durée de l'essai mise à jour:** {minutes} minutes")
        
    except Exception as e:
        await event.respond(f"❌ Erreur: {e}")

@client.on(events.NewMessage(pattern='/trials'))
async def cmd_trials(event):
    if event.sender_id != ADMIN_ID:
        return
    
    trial_users = []
    for user_id_str, user_info in users_data.items():
        user_id = int(user_id_str)
        if is_trial_active(user_id):
            remaining = get_trial_time_remaining(user_id)
            nom = user_info.get('prenom', '') or 'N/A'
            prenom = user_info.get('nom', '') or 'N/A'
            trial_users.append(f"🆔 `{user_id}` | {nom} {prenom} | ⏳ {format_seconds(remaining)}")
    
    if not trial_users:
        await event.respond("📊 Aucun utilisateur en période d'essai actif.")
        return
    
    chunk_size = 50
    for i in range(0, len(trial_users), chunk_size):
        chunk = trial_users[i:i+chunk_size]
        chunk_text = '\n'.join(chunk)
        header = f"🎁 **UTILISATEURS EN ESSAI** ({i+1}-{min(i+len(chunk), len(trial_users))}/{len(trial_users)})\n\n"
        body = chunk_text + "\n\n"
        footer = "💡 `/extendtrial ID minutes` | `/canceltrial ID` | `/userinfo ID`"
        await event.respond(header + body + footer)
        await asyncio.sleep(0.5)

@client.on(events.NewMessage(pattern=r'^/extendtrial (\d+) (\d+)$'))
async def cmd_extendtrial(event):
    if event.sender_id != ADMIN_ID:
        return
    
    try:
        user_id = int(event.pattern_match.group(1))
        additional_minutes = int(event.pattern_match.group(2))
        
        if str(user_id) not in users_data:
            await event.respond(f"❌ Utilisateur {user_id} non trouvé.")
            return
        
        user = get_user(user_id)
        if not is_trial_active(user_id):
            await event.respond(f"❌ L'utilisateur {user_id} n'est pas en essai actif.")
            return
        
        current_end = datetime.fromisoformat(user['trial_joined_at']) + timedelta(minutes=get_trial_duration())
        new_end = current_end + timedelta(minutes=additional_minutes)
        new_start = new_end - timedelta(minutes=get_trial_duration())
        update_user(user_id, {'trial_joined_at': new_start.isoformat()})
        
        await event.respond(f"""✅ **ESSAI PROLONGÉ**

🆔 **ID:** `{user_id}`
👤 **Nom:** {user.get('prenom', '')} {user.get('nom', '')}
⏱️ **Temps ajouté:** {additional_minutes} minutes
📅 **Nouvelle fin:** {new_end.strftime('%d/%m/%Y %H:%M:%S')}""")
        
        try:
            await client.send_message(user_id, f"""⏱️ **VOTRE ESSAI A ÉTÉ PROLONGÉ!**

✅ {additional_minutes} minutes ajoutées!
📅 Nouvelle fin: {new_end.strftime('%H:%M:%S')}

🚀 Profitez bien!""")
        except:
            pass
        
    except Exception as e:
        await event.respond(f"❌ Erreur: {e}")

@client.on(events.NewMessage(pattern=r'^/canceltrial (\d+)$'))
async def cmd_canceltrial(event):
    if event.sender_id != ADMIN_ID:
        return
    
    try:
        user_id = int(event.pattern_match.group(1))
        
        if str(user_id) not in users_data:
            await event.respond(f"❌ Utilisateur {user_id} non trouvé.")
            return
        
        user = get_user(user_id)
        if not is_trial_active(user_id):
            await event.respond(f"❌ L'utilisateur {user_id} n'est pas en essai actif.")
            return
        
        vip_channel_id = get_vip_channel_id()
        try:
            entity = await client.get_input_entity(vip_channel_id)
            await client.kick_participant(entity, user_id)
            await client(EditBannedRequest(
                channel=entity,
                participant=user_id,
                banned_rights=ChatBannedRights(until_date=None, view_messages=False)
            ))
        except Exception as e:
            logger.error(f"Erreur expulsion: {e}")
        
        update_user(user_id, {
            'trial_used': True,
            'is_in_channel': False,
            'trial_joined_at': None
        })
        
        await event.respond(f"""🚫 **ESSAI ANNULÉ**

🆔 **ID:** `{user_id}`
👤 **Nom:** {user.get('prenom', '')} {user.get('nom', '')}

L'utilisateur a été expulsé immédiatement.""")
        
        try:
            await client.send_message(user_id, """❌ **VOTRE ESSAI A ÉTÉ ANNULÉ**

Vous avez été retiré du canal VIP.

💳 Pour réintégrer le canal, payez maintenant:
/payer""")
        except:
            pass
        
    except Exception as e:
        await event.respond(f"❌ Erreur: {e}")

# ============================================================
# COMMANDES ADMIN - GESTION DES ABONNÉS (inchangées)
# ============================================================

@client.on(events.NewMessage(pattern='/subscribers'))
async def cmd_subscribers(event):
    if event.sender_id != ADMIN_ID:
        return
    
    sub_users = []
    for user_id_str, user_info in users_data.items():
        user_id = int(user_id_str)
        if is_user_subscribed(user_id):
            remaining_str = format_time_remaining(user_info.get('subscription_end'))
            nom = user_info.get('prenom', '') or 'N/A'
            prenom = user_info.get('nom', '') or 'N/A'
            total_added = user_info.get('total_time_added', 0)
            sub_users.append(f"🆔 `{user_id}` | {nom} {prenom} | ⏳ {remaining_str} | 📊 {total_added}min")
    
    if not sub_users:
        await event.respond("📊 Aucun abonné actif.")
        return
    
    chunk_size = 50
    for i in range(0, len(sub_users), chunk_size):
        chunk = sub_users[i:i+chunk_size]
        chunk_text = '\n'.join(chunk)
        header = f"✅ **ABONNÉS ACTIFS** ({i+1}-{min(i+len(chunk), len(sub_users))}/{len(sub_users)})\n\n"
        body = chunk_text + "\n\n"
        footer = "💡 `/addtime ID durée` | `/removetime ID` | `/userinfo ID`"
        await event.respond(header + body + footer)
        await asyncio.sleep(0.5)

@client.on(events.NewMessage(pattern=r'^/addtime (\d+) (.+)$'))
async def cmd_addtime(event):
    if event.sender_id != ADMIN_ID:
        return
    
    try:
        user_id = int(event.pattern_match.group(1))
        duration_input = event.pattern_match.group(2).strip()
        
        if str(user_id) not in users_data:
            await event.respond(f"❌ Utilisateur {user_id} non trouvé.")
            return
        
        additional_minutes = parse_duration(duration_input)
        if additional_minutes == 0:
            await event.respond("❌ Format de durée invalide.")
            return
        
        user = get_user(user_id)
        
        if not is_user_subscribed(user_id):
            await add_user_to_vip(user_id, additional_minutes)
            await event.respond(f"""✅ **NOUVEL ABONNEMENT CRÉÉ**

🆔 **ID:** `{user_id}`
👤 **Nom:** {user.get('prenom', '')} {user.get('nom', '')}
⏱️ **Durée:** {additional_minutes} minutes""")
            return
        
        current_end = datetime.fromisoformat(user['subscription_end'])
        new_end = current_end + timedelta(minutes=additional_minutes)
        
        update_user(user_id, {
            'subscription_end': new_end.isoformat(),
            'vip_expires_at': new_end.isoformat(),
            'total_time_added': user.get('total_time_added', 0) + additional_minutes
        })
        
        await event.respond(f"""✅ **TEMPS AJOUTÉ**

🆔 **ID:** `{user_id}`
👤 **Nom:** {user.get('prenom', '')} {user.get('nom', '')}
⏱️ **Temps ajouté:** {additional_minutes} minutes
📅 **Nouvelle fin:** {new_end.strftime('%d/%m/%Y %H:%M:%S')}
📊 **Total ajouté:** {user.get('total_time_added', 0) + additional_minutes} minutes""")
        
        try:
            await client.send_message(user_id, f"""⏱️ **TEMPS AJOUTÉ À VOTRE ABONNEMENT!**

✅ {additional_minutes} minutes ajoutées!
📅 Nouvelle fin: {new_end.strftime('%d/%m/%Y %H:%M')}

🚀 Profitez bien!""")
        except:
            pass
        
    except Exception as e:
        await event.respond(f"❌ Erreur: {e}")

@client.on(events.NewMessage(pattern=r'^/removetime (\d+)$'))
async def cmd_removetime(event):
    if event.sender_id != ADMIN_ID:
        return
    
    try:
        user_id = int(event.pattern_match.group(1))
        
        if str(user_id) not in users_data:
            await event.respond(f"❌ Utilisateur {user_id} non trouvé.")
            return
        
        user = get_user(user_id)
        if not is_user_subscribed(user_id):
            await event.respond(f"❌ L'utilisateur {user_id} n'est pas abonné.")
            return
        
        vip_channel_id = get_vip_channel_id()
        try:
            entity = await client.get_input_entity(vip_channel_id)
            await client.kick_participant(entity, user_id)
            await client(EditBannedRequest(
                channel=entity,
                participant=user_id,
                banned_rights=ChatBannedRights(until_date=None, view_messages=False)
            ))
        except Exception as e:
            logger.error(f"Erreur expulsion: {e}")
        
        update_user(user_id, {
            'subscription_end': None,
            'vip_expires_at': None,
            'vip_duration_minutes': None,
            'is_in_channel': False
        })
        
        await event.respond(f"""🚫 **ABONNEMENT TERMINÉ**

🆔 **ID:** `{user_id}`
👤 **Nom:** {user.get('prenom', '')} {user.get('nom', '')}

L'utilisateur a été expulsé immédiatement.""")
        
        try:
            await client.send_message(user_id, """❌ **VOTRE ABONNEMENT A ÉTÉ TERMINÉ**

Vous avez été retiré du canal VIP.

💳 Pour réintégrer le canal, payez maintenant:
/payer""")
        except:
            pass
        
    except Exception as e:
        await event.respond(f"❌ Erreur: {e}")

# ============================================================
# COMMANDE INFO UTILISATEUR (inchangée)
# ============================================================

@client.on(events.NewMessage(pattern=r'^/userinfo (\d+)$'))
async def cmd_userinfo(event):
    if event.sender_id != ADMIN_ID:
        return
    
    try:
        user_id = int(event.pattern_match.group(1))
        
        if str(user_id) not in users_data:
            await event.respond(f"❌ Utilisateur {user_id} non trouvé.")
            return
        
        user = get_user(user_id)
        
        status = get_user_status(user_id)
        time_info = ""
        
        if is_trial_active(user_id):
            remaining = get_trial_time_remaining(user_id)
            time_info = f"\n⏳ **Essai restant:** {format_seconds(remaining)}"
        elif is_user_subscribed(user_id):
            remaining = format_time_remaining(user.get('subscription_end'))
            time_info = f"\n⏳ **Abonnement restant:** {remaining}"
        
        history = f"\n📊 **Total temps ajouté:** {user.get('total_time_added', 0)} minutes"
        
        info = f"""📋 **INFORMATION UTILISATEUR**

🆔 **ID:** `{user_id}`
👤 **Nom:** {user.get('prenom', '')} {user.get('nom', '')}
🌍 **Pays:** {user.get('pays', 'N/A')}
📊 **Statut:** {status}
🔗 **Dans le canal:** {'Oui' if user.get('is_in_channel') else 'Non'}{time_info}{history}

**Dates:**
• Inscription: {user.get('trial_started', 'N/A')[:19] if user.get('trial_started') else 'N/A'}
• Début essai: {user.get('trial_joined_at', 'N/A')[:19] if user.get('trial_joined_at') else 'N/A'}
• Début abonnement: {user.get('vip_joined_at', 'N/A')[:19] if user.get('vip_joined_at') else 'N/A'}

💡 `/addtime {user_id} 2h` | `/removetime {user_id}`"""
        
        await event.respond(info)
        
    except Exception as e:
        await event.respond(f"❌ Erreur: {e}")

# ============================================================
# COMMANDE MONITORING TEMPS RÉEL (inchangée)
# ============================================================

@client.on(events.NewMessage(pattern=r'^/monitor(\s+\d+)?$'))
async def cmd_monitor(event):
    if event.sender_id != ADMIN_ID:
        return
    
    message_text = event.message.message.strip()
    parts = message_text.split()
    
    if len(parts) > 1:
        try:
            user_id = int(parts[1])
            if str(user_id) not in users_data:
                await event.respond(f"❌ Utilisateur {user_id} non trouvé.")
                return
            
            user = get_user(user_id)
            status_lines = []
            
            if is_trial_active(user_id):
                remaining = get_trial_time_remaining(user_id)
                status_lines.append(f"🎁 **ESSAI:** {format_seconds(remaining)}")
            elif is_user_subscribed(user_id):
                remaining = format_time_remaining(user.get('subscription_end'))
                status_lines.append(f"✅ **ABONNEMENT:** {remaining}")
            else:
                status_lines.append("❌ **AUCUN ACCÈS ACTIF**")
            
            await event.respond(f"""📊 **MONITORING** `{user_id}`

{' | '.join(status_lines)}

👤 {user.get('prenom', '')} {user.get('nom', '')}""")
            return
            
        except ValueError:
            pass
    
    active_users = []
    
    for user_id_str, user_info in users_data.items():
        user_id = int(user_id_str)
        line = f"🆔 `{user_id}`"
        
        if is_trial_active(user_id):
            remaining = get_trial_time_remaining(user_id)
            line += f" | 🎁 {format_seconds(remaining)}"
            active_users.append((remaining, line))
        elif is_user_subscribed(user_id):
            remaining_str = format_time_remaining(user_info.get('subscription_end'))
            try:
                expiry = datetime.fromisoformat(user_info.get('subscription_end'))
                remaining_secs = int((expiry - datetime.now()).total_seconds())
                line += f" | ✅ {remaining_str}"
                active_users.append((remaining_secs, line))
            except:
                line += f" | ✅ {remaining_str}"
                active_users.append((999999, line))
    
    if not active_users:
        await event.respond("📊 Aucun utilisateur actif à monitorer.")
        return
    
    active_users.sort(key=lambda x: x[0])
    top_users = [line for _, line in active_users[:20]]
    
    header = "⏱️ **MONITORING TEMPS RÉEL** (Top 20 - expirent bientôt)\n\n"
    users_text = '\n'.join(top_users)
    footer = f"\n\n💡 `/monitor ID` pour détails spécifiques"
    
    await event.respond(header + users_text + footer)

@client.on(events.NewMessage(pattern='/users'))
async def cmd_users(event):
    if event.is_group or event.is_channel: 
        return

    if event.sender_id != ADMIN_ID:
        await event.respond("❌ Commande réservée à l'administrateur.")
        return

    if not users_data:
        await event.respond("📊 Aucun utilisateur.")
        return

    users_list = []
    for user_id_str, user_info in users_data.items():
        user_id = int(user_id_str)
        nom = user_info.get('nom', 'N/A') or 'N/A'
        prenom = user_info.get('prenom', 'N/A') or 'N/A'
        status = get_user_status(user_id)

        vip_info = ""
        if user_info.get('vip_expires_at'):
            vip_remaining = format_time_remaining(user_info['vip_expires_at'])
            vip_info = f" | VIP: {vip_remaining}"

        user_line = f"🆔 `{user_id}` | {prenom} {nom} | {status}{vip_info}"
        users_list.append(user_line)

    chunk_size = 50
    for i in range(0, len(users_list), chunk_size):
        chunk = users_list[i:i+chunk_size]
        chunk_text = '\n'.join(chunk)
        await event.respond(f"""📋 **UTILISATEURS** ({i+1}-{min(i+len(chunk), len(users_list))}/{len(users_list)})

{chunk_text}

💡 `/userinfo ID` | `/monitor` | `/settime ID DURÉE`""")
        await asyncio.sleep(0.5)

@client.on(events.NewMessage(pattern=r'^/msg (\d+)$'))
async def cmd_msg(event):
    if event.is_group or event.is_channel: 
        return

    if event.sender_id != ADMIN_ID:
        await event.respond("❌ Commande réservée à l'administrateur.")
        return

    try:
        target_user_id = int(event.pattern_match.group(1))

        if str(target_user_id) not in users_data:
            await event.respond(f"❌ Utilisateur {target_user_id} non trouvé.")
            return

        user_info = users_data[str(target_user_id)]

        admin_message_state[event.sender_id] = {
            'target_user_id': target_user_id,
            'step': 'awaiting_message'
        }

        await event.respond(f"""✉️ **Message à {user_info.get('prenom', '')}** (ID: `{target_user_id}`)

📝 Écrivez votre message:""")

    except Exception as e:
        await event.respond(f"❌ Erreur: {e}")

@client.on(events.NewMessage(pattern='/status'))
async def cmd_status(event):
    if event.is_group or event.is_channel: 
        return

    user_id = event.sender_id
    user = get_user(user_id)

    if not user.get('registered'):
        await event.respond("❌ Utilisez /start pour vous inscrire.")
        return

    response = f"""📊 **VOTRE STATUT**

👤 **Nom:** {user.get('prenom', '')} {user.get('nom', '')}
🌍 **Pays:** {user.get('pays', 'N/A')}
📊 **Statut:** {get_user_status(user_id)}"""

    if user.get('subscription_end'):
        remaining = format_time_remaining(user['subscription_end'])
        response += f"\n📅 **Abonnement:** {remaining}"

    if is_trial_active(user_id):
        remaining = get_trial_time_remaining(user_id)
        response += f"\n🎁 **Essai:** {format_seconds(remaining)}"

    response += "\n\n💡 `/payer` pour renouveler"

    await event.respond(response)

@client.on(events.NewMessage(pattern='/bilan'))
async def cmd_bilan(event):
    if event.is_group or event.is_channel: 
        return
    if event.sender_id != ADMIN_ID: 
        return

    if stats_bilan['total'] == 0:
        await event.respond("📊 Aucune prédiction.")
        return

    win_rate = (stats_bilan['wins'] / stats_bilan['total']) * 100

    await event.respond(f"""📊 **BILAN**

🎯 Total: {stats_bilan['total']}
✅ Victoires: {stats_bilan['wins']} ({win_rate:.1f}%)
❌ Défaites: {stats_bilan['losses']}

**Détails:**
• Immédiates: {stats_bilan['win_details'].get('✅0️⃣', 0)}
• 2ème: {stats_bilan['win_details'].get('✅1️⃣', 0)}
• 3ème: {stats_bilan['win_details'].get('✅2️⃣', 0)}
• 4ème: {stats_bilan['win_details'].get('✅3️⃣', 0)}""")

@client.on(events.NewMessage(pattern='/reset'))
async def cmd_reset_all(event):
    if event.is_group or event.is_channel: 
        return
    if event.sender_id != ADMIN_ID:
        await event.respond("❌ Admin uniquement")
        return

    global users_data, pending_predictions, processed_messages
    global current_game_number, last_source_game_number, stats_bilan
    global already_predicted_games, pending_payments, verification_state
    global pending_finalization

    users_data = {}
    save_users_data()
    pending_predictions.clear()
    processed_messages.clear()
    already_predicted_games.clear()
    pending_payments.clear()
    pending_finalization.clear()
    
    # Réinitialiser l'état de vérification
    verification_state = {
        'predicted_number': None,
        'predicted_suit': None,
        'current_check': 0,
        'message_id': None,
        'channel_id': None,
        'status': None
    }

    current_game_number = 0
    last_source_game_number = 0

    stats_bilan = {
        'total': 0, 'wins': 0, 'losses': 0,
        'win_details': {'✅0️⃣': 0, '✅1️⃣': 0, '✅2️⃣': 0, '✅3️⃣': 0},
        'loss_details': {'❌': 0}
    }

    await event.respond("🚨 **RESET EFFECTUÉ**")

@client.on(events.NewMessage(pattern='/help'))
async def cmd_help(event):
    if event.is_group or event.is_channel: 
        return

    admin_cmds = ""
    if event.sender_id == ADMIN_ID:
        admin_cmds = """
**Admin - Général:**
/users - Liste tous les utilisateurs
/userinfo ID - Détails d'un utilisateur
/monitor - Monitorer temps restant
/monitor ID - Détails spécifiques
/msg ID - Envoyer message privé

**Admin - Prédictions:**
/verifstatus - Voir vérification en cours
/clearverif - Effacer vérification bloquée
/predictinfo - Info système prédiction
/stop - Arrêter prédictions
/resume - Reprendre prédictions
/setnext NUM COSTUME - Forcer prédiction

**Admin - Essai:**
/trials - Liste essais actifs
/settrialtime 15 - Durée essai (min)
/extendtrial ID min - Prolonger essai
/canceltrial ID - Annuler essai

**Admin - Abonnés:**
/subscribers - Liste abonnés actifs
/addtime ID durée - Ajouter temps
/removetime ID - Retirer et expulser

**Admin - Système:**
/setchannel TYPE ID - Configurer canaux
/channels - Voir config canaux
/pausecycle - Configurer cycle pause
/bilan - Statistiques
/reset - Tout réinitialiser
"""

    await event.respond(f"""📖 **AIDE**

**Utilisateur:**
/start - Inscription (15min essai)
/status - Voir temps restant
/payer - Renouveler
/help - Cette aide

{admin_cmds}
**Support:** @Kouamappoloak""")

@client.on(events.NewMessage(pattern='/payer'))
async def cmd_payer(event):
    if event.is_group or event.is_channel: 
        return

    user_id = event.sender_id
    user = get_user(user_id)

    if not user.get('registered'):
        await event.respond("❌ Inscrivez-vous avec /start")
        return

    buttons = [
        [Button.url("💳 24H - 200 FCFA", PAYMENT_LINK_24H)],
        [Button.url("🔥 1 SEMAINE - 1000 FCFA", PAYMENT_LINK)],
        [Button.url("💎 2 SEMAINES - 2000 FCFA", PAYMENT_LINK)]
    ]

    await event.respond(f"""💳 **PAIEMENT**

**Étapes:**
1️⃣ Cliquez sur votre formule
2️⃣ Payez
3️⃣ Envoyez la capture ici
4️⃣ L'admin valide
5️⃣ Vous recevez le lien (2 min)

👇 **CHOISISSEZ:**""", buttons=buttons)

    update_user(user_id, {'pending_payment': True, 'awaiting_screenshot': True})

# ============================================================
# GESTION DES MESSAGES ÉDITÉS
# ============================================================

@client.on(events.MessageEdited)
async def handle_edited_message(event):
    """Gère les messages édités dans le canal source"""
    if event.is_group or event.is_channel:
        if event.chat_id == get_source_channel_id():
            logger.info(f"✏️ Message édité détecté dans le canal source")
            await process_source_message(event, is_edit=True)
        return

# ============================================================
# SERVEUR WEB ET DÉMARRAGE
# ============================================================

async def index(request):
    html = f"""<!DOCTYPE html>
<html>
<head>
    <title>Bot Baccarat - Prédictions</title>
    <style>
        body {{ font-family: Arial, sans-serif; background: linear-gradient(135deg, #1e3c72 0%, #2a5298 100%); color: white; text-align: center; padding: 50px; }}
        h1 {{ font-size: 3em; margin-bottom: 20px; }}
        .status {{ background: rgba(255,255,255,0.1); padding: 30px; border-radius: 15px; display: inline-block; margin: 20px; }}
        .number {{ font-size: 2.5em; font-weight: bold; color: #ffd700; }}
        .label {{ font-size: 1.2em; opacity: 0.9; }}
    </style>
</head>
<body>
    <h1>🎰 Bot Baccarat - Prédictions</h1>
    <div class="status">
        <div class="label">Jeu Actuel</div>
        <div class="number">#{current_game_number}</div>
    </div>
    <div class="status">
        <div class="label">Utilisateurs</div>
        <div class="number">{len(users_data)}</div>
    </div>
    <div class="status">
        <div class="label">Vérification Active</div>
        <div class="number">{verification_state['predicted_number'] if verification_state['predicted_number'] else 'Aucune'}</div>
    </div>
    <div class="status">
        <div class="label">En Pause</div>
        <div class="number">{'Oui' if is_currently_paused() else 'Non'}</div>
    </div>
    <div class="status">
        <div class="label">Canal Source</div>
        <div class="number">{get_source_channel_id()}</div>
    </div>
    <div class="status">
        <div class="label">Canal Prédiction</div>
        <div class "number">{get_prediction_channel_id()}</div>
    </div>
    <p style="margin-top: 40px;">✅ Système opérationnel | Essai: {get_trial_duration()}min | Vérification: N→N+1→N+2→N+3</p>
</body>
</html>"""
    return web.Response(text=html, content_type='text/html', status=200)

async def health_check(request):
    return web.Response(text="OK", status=200)

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', index)
    app.router.add_get('/health', health_check)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start() 

async def schedule_daily_reset():
    wat_tz = timezone(timedelta(hours=1)) 
    reset_time = time(0, 59, tzinfo=wat_tz)

    while True:
        now = datetime.now(wat_tz)
        await asyncio.sleep(3600)

@client.on(events.NewMessage(pattern=r'^/start$'))
async def cmd_start(event):
    logger.info(f"Commande /start reçue de {event.sender_id}")
    if event.is_group or event.is_channel: 
        return

    user_id = event.sender_id
    user = get_user(user_id)

    if user.get('registered'):
        await event.respond(f"""👋 **RE-BONJOUR {user.get('prenom', '')}!**

🚀 Votre compte est déjà actif.
📊 Statut: {get_user_status(user_id)}

💡 Utilisez /status pour voir votre temps restant.""")
        return

    user_conversation_state[user_id] = 'awaiting_nom'

    welcome_msg = f"""👋 **BIENVENUE SUR LE BOT BACCARAT!**

🚀 **Système de Prédiction Automatique**
• Numéros pairs (6-1436, sauf finissant par 0)
• Cycle de costumes: ♥ ♦ ♣ ♠ ♦ ♥ ♠ ♣
• Pause auto après 5 prédictions
• Vérification automatique: N → N+1 → N+2 → N+3

📝 **Étape 1/3: Quel est votre nom de famille?**"""

    await event.respond(welcome_msg)

async def main():
    while True:
        try:
            load_channels_config()
            load_vip_config()
            load_users_data()
            load_pause_config()
            load_trial_config()
            
            await start_web_server()
            
            await client.start(bot_token=BOT_TOKEN)
            
            logger.info("Récupération des dialogues pour le cache d'entités...")
            try:
                await client.get_entity(get_source_channel_id())
                await client.get_entity(get_prediction_channel_id())
                await client.get_entity(get_vip_channel_id())
            except Exception as e:
                logger.warning(f"Note: Certains canaux ne sont pas encore accessibles (normal pour un bot): {e}")
            
            me = await client.get_me()
            logger.info(f"Connecté en tant que: {me.username} (ID: {me.id})")
            
            logger.info("Bot démarré avec succès!")
            
            await client.run_until_disconnected()
            
        except ConnectionError:
            logger.warning("Connexion perdue, tentative de reconnexion...")
            await asyncio.sleep(5)
        except Exception as e:
            if "A wait of" in str(e):
                import re
                match = re.search(r"(\d+) seconds", str(e))
                wait_seconds = int(match.group(1)) if match else 300
                logger.error(f"FloodWait: Attente de {wait_seconds} secondes...")
                await asyncio.sleep(wait_seconds + 5)
            else:
                logger.error(f"Erreur main: {e}")
                await asyncio.sleep(30)

if __name__ == '__main__':
    asyncio.run(main())
