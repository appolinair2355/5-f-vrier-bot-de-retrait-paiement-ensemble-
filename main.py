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
DEFAULT_SOURCE_CHANNEL_ID = -1002682552255  # Canal Source (reçoit les jeux)
DEFAULT_PREDICTION_CHANNEL_ID = -1003502536129  # Canal Prédiction (envoie les prédictions)
DEFAULT_VIP_CHANNEL_ID = -1003502536129  # Canal VIP (accès payant)
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

# Configuration des canaux (modifiable par l'admin)
channels_config = {
    'source_channel_id': DEFAULT_SOURCE_CHANNEL_ID,
    'prediction_channel_id': DEFAULT_PREDICTION_CHANNEL_ID,
    'vip_channel_id': DEFAULT_VIP_CHANNEL_ID,
    'vip_channel_link': DEFAULT_VIP_CHANNEL_LINK
}

# Configuration par défaut des pauses (en secondes)
DEFAULT_PAUSE_CYCLE = [180, 240, 420]  # 3min, 4min, 7min
pause_config = {
    'cycle': DEFAULT_PAUSE_CYCLE.copy(),
    'current_index': 0,
    'last_prediction_time': None,
    'predictions_count': 0,
    'is_paused': False,
    'pause_end_time': None,
    'just_resumed': False
}

# Configuration de l'essai
DEFAULT_TRIAL_DURATION = 15  # 15 minutes par défaut
trial_config = {
    'duration_minutes': DEFAULT_TRIAL_DURATION,
    'link_visible_seconds': 10  # 10 secondes
}

# Configuration VIP
vip_config = {
    'channel_id': DEFAULT_VIP_CHANNEL_ID,
    'channel_link': DEFAULT_VIP_CHANNEL_LINK
}

pending_predictions = {}
queued_predictions = {}
processed_messages = set()
current_game_number = 0
last_source_game_number = 0

# NOUVEAU: Gestion des prédictions et vérification
current_prediction_target = None  # Une seule prédiction active à la fois
last_predicted_number = None
pending_finalization = {}  # Messages en attente de finalisation

# Cycle des costumes: ♥, ♠, ♦, ♣
SUIT_CYCLE = ['♥', '♠', '♦', '♣']

# Stats
already_predicted_games = set()
stats_bilan = {
    'total': 0,
    'wins': 0,
    'losses': 0,
    'win_details': {'✅0️⃣': 0, '✅1️⃣': 0, '✅2️⃣': 0},
    'loss_details': {'❌': 0}
}

# --- Système de Paiement ---
users_data = {}
user_conversation_state = {}
pending_payments = {}
admin_setting_time = {}
admin_message_state = {}

predictions_enabled = True

# ============================================================
# CONFIGURATION DE L'ESSAI (NOUVEAU)
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
# GESTION DES NUMÉROS PAIRS VALIDES (NOUVEAU)
# ============================================================

def get_valid_even_numbers():
    """Génère la liste des numéros pairs valides (6-1436, sauf finissant par 0)."""
    valid_numbers = []
    for num in range(6, 1437):
        if num % 2 == 0 and num % 10 != 0:
            valid_numbers.append(num)
    return valid_numbers

VALID_EVEN_NUMBERS = get_valid_even_numbers()

def get_suit_for_number(number):
    """Attribue un costume selon le cycle."""
    if number not in VALID_EVEN_NUMBERS:
        return None
    idx = VALID_EVEN_NUMBERS.index(number) % len(SUIT_CYCLE)
    return SUIT_CYCLE[idx]

def get_next_prediction_number(after_number):
    """Trouve le prochain numéro pair valide après un numéro donné."""
    for num in VALID_EVEN_NUMBERS:
        if num > after_number:
            return num
    return None

def get_previous_odd_number(even_number):
    """Retourne le numéro impair précédent un numéro pair."""
    return even_number - 1

def is_valid_prediction_number(number):
    """Vérifie si un numéro est valide pour prédiction."""
    return number in VALID_EVEN_NUMBERS

# ============================================================
# GESTION DES PAUSES (NOUVEAU)
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
    """Retourne la durée de la prochaine pause selon le cycle."""
    cycle = pause_config['cycle']
    idx = pause_config['current_index'] % len(cycle)
    return cycle[idx]

def increment_pause_index():
    """Incrémente l'index du cycle de pause."""
    pause_config['current_index'] += 1
    save_pause_config()

def should_pause():
    """Vérifie si on doit faire une pause après 5 prédictions."""
    return pause_config['predictions_count'] >= 5

def start_pause():
    """Démarre une pause."""
    duration = get_next_pause_duration()
    pause_config['is_paused'] = True
    pause_config['pause_end_time'] = (datetime.now() + timedelta(seconds=duration)).isoformat()
    pause_config['predictions_count'] = 0
    increment_pause_index()
    save_pause_config()
    logger.info(f"⏸️ Pause démarrée pour {duration} secondes")
    return duration

def is_currently_paused():
    """Vérifie si on est actuellement en pause."""
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
    """Retourne le temps de pause restant en secondes."""
    if not is_currently_paused():
        return 0
    try:
        end_time = datetime.fromisoformat(pause_config['pause_end_time'])
        remaining = (end_time - datetime.now()).total_seconds()
        return max(0, int(remaining))
    except:
        return 0

def record_prediction():
    """Enregistre qu'une prédiction a été faite."""
    pause_config['predictions_count'] += 1
    pause_config['last_prediction_time'] = datetime.now().isoformat()
    save_pause_config()

def reset_pause_counter():
    """Réinitialise le compteur de prédictions."""
    pause_config['predictions_count'] = 0
    save_pause_config()

# ============================================================
# GESTION DES CANAUX (CONFIGURABLE PAR L'ADMIN)
# ============================================================

def load_channels_config():
    """Charge la configuration des canaux."""
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
    """Sauvegarde la configuration des canaux."""
    try:
        with open(CHANNELS_CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(channels_config, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Erreur sauvegarde channels_config: {e}")

def get_source_channel_id():
    """Retourne l'ID du canal source."""
    return channels_config.get('source_channel_id', DEFAULT_SOURCE_CHANNEL_ID)

def get_prediction_channel_id():
    """Retourne l'ID du canal de prédiction."""
    return channels_config.get('prediction_channel_id', DEFAULT_PREDICTION_CHANNEL_ID)

def get_vip_channel_id():
    """Retourne l'ID du canal VIP."""
    return channels_config.get('vip_channel_id', DEFAULT_VIP_CHANNEL_ID)

def get_vip_channel_link():
    """Retourne le lien du canal VIP."""
    return channels_config.get('vip_channel_link', DEFAULT_VIP_CHANNEL_LINK)

def set_source_channel(channel_id: int):
    """Définit le canal source."""
    channels_config['source_channel_id'] = channel_id
    save_channels_config()
    logger.info(f"Canal source mis à jour: {channel_id}")

def set_prediction_channel(channel_id: int):
    """Définit le canal de prédiction."""
    channels_config['prediction_channel_id'] = channel_id
    save_channels_config()
    logger.info(f"Canal prédiction mis à jour: {channel_id}")

def set_vip_channel(channel_id: int, channel_link: str):
    """Définit le canal VIP."""
    channels_config['vip_channel_id'] = channel_id
    channels_config['vip_channel_link'] = channel_link
    vip_config['channel_id'] = channel_id
    vip_config['channel_link'] = channel_link
    save_channels_config()
    save_vip_config()
    logger.info(f"Canal VIP mis à jour: ID={channel_id}")

def reset_channels_config():
    """Réinitialise tous les canaux aux valeurs par défaut."""
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
            'trial_joined_at': None,  # Nouveau: quand il a rejoint le canal
            'subscription_end': None,
            'subscription_type': None,
            'pending_payment': False,
            'awaiting_screenshot': False,
            'awaiting_amount': False,
            'vip_expires_at': None,
            'vip_duration_minutes': None,
            'vip_joined_at': None,
            'is_in_channel': False,  # Nouveau: statut dans le canal
            'total_time_added': 0  # Nouveau: temps total ajouté (en minutes)
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
    """Retourne le temps d'essai restant en secondes."""
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
    """Formate des secondes en format lisible."""
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
    """Ajoute un utilisateur en période d'essai au canal VIP."""
    try:
        trial_duration = get_trial_duration()
        now = datetime.now()
        expires_at = now + timedelta(minutes=trial_duration)

        update_user(user_id, {
            'trial_joined_at': now.isoformat(),
            'is_in_channel': True,
            'trial_used': False
        })

        # Envoyer le lien à l'utilisateur (disparaît après 10 secondes)
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

        # Programmer la suppression du message après 10 secondes
        asyncio.create_task(delete_message_after_delay(user_id, link_msg.id, 10))

        # Notification à l'admin
        user = get_user(user_id)
        await client.send_message(ADMIN_ID, f"""🆕 **NOUVEL UTILISATEUR EN ESSAI**

👤 **Nom:** {user.get('prenom', '')} {user.get('nom', '')}
🆔 **ID:** `{user_id}`
📍 **Pays:** {user.get('pays', 'N/A')}
⏳ **Durée:** {trial_duration} minutes
📅 **Expire le:** {expires_at.strftime('%d/%m/%Y %H:%M:%S')}

🔗 Lien envoyé (suppression dans 10s)""")

        # Lancer l'expulsion automatique après l'essai
        asyncio.create_task(auto_kick_trial_user(user_id, trial_duration * 60))

        logger.info(f"Utilisateur {user_id} ajouté en essai pour {trial_duration} minutes")
        return True

    except Exception as e:
        logger.error(f"Erreur ajout utilisateur {user_id} en essai: {e}")
        return False

async def auto_kick_trial_user(user_id: int, delay_seconds: int):
    """Expulse automatiquement l'utilisateur du canal après la période d'essai."""
    await asyncio.sleep(delay_seconds)

    try:
        user = get_user(user_id)
        
        # Vérifier si l'utilisateur a souscrit entre-temps
        if is_user_subscribed(user_id):
            logger.info(f"Utilisateur {user_id} a souscrit, annulation de l'expulsion d'essai")
            return
        
        if not user.get('trial_joined_at'):
            return

        vip_channel_id = get_vip_channel_id()
        
        # Expulser du canal
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

        # Message de paiement à l'utilisateur
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

        # Notification à l'admin
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
    """Ajoute un utilisateur abonné au canal VIP."""
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

        # Envoyer le lien à l'utilisateur (disparaît après 2 minutes pour abonnés)
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

        # Programmer la suppression du message après 2 minutes
        asyncio.create_task(delete_message_after_delay(user_id, link_msg.id, 120))

        # Notification à l'admin
        user = get_user(user_id)
        await client.send_message(ADMIN_ID, f"""✅ **UTILISATEUR ABONNÉ AU CANAL VIP**

👤 **Nom:** {user.get('prenom', '')} {user.get('nom', '')}
🆔 **ID:** `{user_id}`
⏳ **Temps restant:** {time_str}
📅 **Expire le:** {expires_at.strftime('%d/%m/%Y %H:%M:%S')}
⏱️ **Durée totale ajoutée:** {user.get('total_time_added', 0) + duration_minutes} min

🔗 Lien envoyé (sera supprimé dans 2 min)""")

        # Lancer l'expulsion automatique
        asyncio.create_task(auto_kick_user(user_id, duration_minutes * 60))

        logger.info(f"Utilisateur {user_id} ajouté au canal VIP pour {duration_minutes} minutes")
        return True

    except Exception as e:
        logger.error(f"Erreur ajout utilisateur {user_id} au VIP: {e}")
        return False

async def delete_message_after_delay(chat_id: int, message_id: int, delay_seconds: int):
    """Supprime un message après un délai."""
    await asyncio.sleep(delay_seconds)
    try:
        await client.delete_messages(chat_id, [message_id])
        logger.info(f"Message {message_id} supprimé après {delay_seconds}s")
    except Exception as e:
        logger.error(f"Erreur suppression message {message_id}: {e}")

async def auto_kick_user(user_id: int, delay_seconds: int):
    """Expulse automatiquement l'utilisateur du canal après le délai."""
    await asyncio.sleep(delay_seconds)

    try:
        user = get_user(user_id)
        if not user.get('vip_expires_at'):
            return

        vip_channel_id = get_vip_channel_id()
        
        # S'assurer que l'entité du canal est connue
        try:
            entity = await client.get_input_entity(vip_channel_id)
        except Exception as e:
            logger.error(f"Impossible de trouver l'entité du canal {vip_channel_id}: {e}")
            await client.get_dialogs()
            entity = await client.get_input_entity(vip_channel_id)

        # Expulser du canal
        await client.kick_participant(entity, user_id)

        # Ré-autoriser pour qu'il puisse revenir
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

        # Notifier l'utilisateur
        try:
            buttons = [
                [Button.url("💳 Renouveler", PAYMENT_LINK)]
            ]
            await client.send_message(user_id, """❌ **VOTRE ABONNEMENT EST TERMINÉ**

Vous avez été retiré du canal VIP.

💳 Pour réintégrer le canal, payez maintenant:""", buttons=buttons)
        except:
            pass

        # Notifier l'admin
        await client.send_message(ADMIN_ID, f"""🚫 **ABONNEMENT TERMINÉ - UTILISATEUR RETIRÉ**

🆔 **ID:** `{user_id}`
👤 **Nom:** {user.get('prenom', '')} {user.get('nom', '')}

L'utilisateur a été expulsé du canal VIP.""")

        logger.info(f"Utilisateur {user_id} expulsé du canal VIP (abonnement terminé)")

    except Exception as e:
        logger.error(f"Erreur expulsion utilisateur {user_id}: {e}")

# ============================================================
# SYSTÈME DE PRÉDICTION CORRIGÉ
# ============================================================

async def send_prediction(target_game, predicted_suit, base_game):
    """Envoie une prédiction dans le canal de prédiction."""
    global current_prediction_target, last_predicted_number
    
    if not predictions_enabled:
        logger.info("Prédictions désactivées, envoi annulé.")
        return False
    
    try:
        prediction_channel_id = get_prediction_channel_id()
        entity = await client.get_input_entity(prediction_channel_id)
        
        prediction_msg = f"""🎰 **PRÉDICTION #{target_game}**
🎯 Couleur: {SUIT_DISPLAY.get(predicted_suit, predicted_suit)}
⏳ Statut: EN ATTENTE..."""
        
        sent_msg = await client.send_message(entity, prediction_msg)
        
        current_prediction_target = {
            'game_number': target_game,
            'suit': predicted_suit,
            'base_game': base_game,
            'message_id': sent_msg.id,
            'channel_id': prediction_channel_id,
            'status': 'pending',
            'checks': 0
        }
        
        last_predicted_number = target_game
        record_prediction()
        
        logger.info(f"✅ Prédiction envoyée: #{target_game} -> {predicted_suit}")
        return True
        
    except Exception as e:
        logger.error(f"❌ Erreur envoi prédiction: {e}")
        return False

async def update_prediction_status(game_number, status):
    """Met à jour le statut d'une prédiction."""
    global current_prediction_target
    
    if not current_prediction_target:
        return False
    
    if current_prediction_target['game_number'] != game_number:
        return False
    
    try:
        channel_id = current_prediction_target['channel_id']
        message_id = current_prediction_target['message_id']
        suit = current_prediction_target['suit']
        
        if status == "❌":
            status_text = "❌ PERDU"
        elif status.startswith("✅"):
            status_text = f"{status} GAGNÉ"
        else:
            status_text = status
        
        updated_msg = f"""🎰 **PRÉDICTION #{game_number}**
🎯 Couleur: {SUIT_DISPLAY.get(suit, suit)}
📊 Statut: {status_text}"""
        
        await client.edit_message(channel_id, message_id, updated_msg)
        
        # Mise à jour des stats
        if status in ['✅0️⃣', '✅1️⃣', '✅2️⃣']:
            stats_bilan['total'] += 1
            stats_bilan['wins'] += 1
            stats_bilan['win_details'][status] = stats_bilan['win_details'].get(status, 0) + 1
        elif status == '❌':
            stats_bilan['total'] += 1
            stats_bilan['losses'] += 1
            stats_bilan['loss_details']['❌'] = stats_bilan['loss_details'].get('❌', 0) + 1
        
        # Réinitialiser la prédiction courante
        current_prediction_target = None
        
        return True
        
    except Exception as e:
        logger.error(f"Erreur mise à jour prédiction: {e}")
        return False

# ============================================================
# FONCTIONS D'ANALYSE DES MESSAGES (CORRIGÉES)
# ============================================================

def extract_game_number(message):
    """Extrait le numéro de jeu du message."""
    # Cherche #N suivi de chiffres
    match = re.search(r"#N\s*(\d+)", message, re.IGNORECASE)
    if match:
        return int(match.group(1))
    # Cherche aussi # suivi de chiffres au début
    match = re.search(r"^#(\d+)", message)
    if match:
        return int(match.group(1))
    return None

def extract_suits_from_parentheses(message_text):
    """Extrait les costumes du premier groupe de parenthèses."""
    matches = re.findall(r"\(([^)]+)\)", message_text)
    if not matches:
        return []
    
    # Premier groupe de parenthèses
    first_group = matches[0]
    suits = []
    
    # Normalise et cherche les costumes
    normalized = first_group.replace('❤️', '♥').replace('❤', '♥').replace('♥️', '♥')
    normalized = normalized.replace('♠️', '♠').replace('♦️', '♦').replace('♣️', '♣')
    
    for suit in ['♥', '♠', '♦', '♣']:
        if suit in normalized:
            suits.append(suit)
    
    return suits

def is_message_finalized(message_text):
    """Vérifie si un message est finalisé (contient ✅ ou 🔰)."""
    return '✅' in message_text or '🔰' in message_text

def is_message_being_edited(message_text):
    """Vérifie si un message est en cours d'édition (contient ▶️)."""
    return '▶️' in message_text

async def check_prediction_result(source_message_text, target_game_number):
    """
    Vérifie si la prédiction est gagnante.
    Retourne: '✅0️⃣', '✅1️⃣', '✅2️⃣', '❌', ou None
    """
    if not current_prediction_target:
        return None
    
    if current_prediction_target['game_number'] != target_game_number:
        return None
    
    predicted_suit = current_prediction_target['suit']
    current_game = extract_game_number(source_message_text)
    
    if current_game is None:
        return None
    
    # Vérifier le numéro prédit (✅0️⃣)
    if current_game == target_game_number:
        suits = extract_suits_from_parentheses(source_message_text)
        if predicted_suit in suits:
            return '✅0️⃣'
        # Si on est au numéro prédit mais costume pas trouvé, on continue à vérifier
    
    # Vérifier numéro + 1 (✅1️⃣)
    if current_game == target_game_number + 1:
        suits = extract_suits_from_parentheses(source_message_text)
        if predicted_suit in suits:
            return '✅1️⃣'
    
    # Vérifier numéro + 2 (✅2️⃣)
    if current_game == target_game_number + 2:
        suits = extract_suits_from_parentheses(source_message_text)
        if predicted_suit in suits:
            return '✅2️⃣'
        # Si on est au +2 et pas trouvé, c'est perdu
    
    # Si on est au numéro + 3 ou plus, c'est perdu
    if current_game >= target_game_number + 3:
        return '❌'
    
    return None

# ============================================================
# TRAITEMENT DES MESSAGES SOURCE (CORRIGÉ)
# ============================================================

async def process_source_message(event):
    """Traite les messages reçus du canal source."""
    global current_game_number, last_source_game_number, current_prediction_target
    
    try:
        message_text = event.message.message
        logger.info(f"📩 Message source reçu: {message_text[:100]}...")
        
        # Vérifier si c'est un message en édition (▶️)
        if is_message_being_edited(message_text):
            game_num = extract_game_number(message_text)
            if game_num:
                logger.info(f"⏳ Message #{game_num} en édition, mise en attente...")
                pending_finalization[game_num] = message_text
            return
        
        # Vérifier si c'est un message finalisé (✅ ou 🔰)
        if not is_message_finalized(message_text):
            logger.info("Message non finalisé ignoré")
            return
        
        game_number = extract_game_number(message_text)
        if game_number is None:
            logger.info("Numéro de jeu non détecté")
            return
        
        current_game_number = game_number
        last_source_game_number = game_number
        
        # Vérifier si ce message était en attente de finalisation
        if game_number in pending_finalization:
            del pending_finalization[game_number]
        
        logger.info(f"🎲 Jeu finalisé détecté: #{game_number}")
        
        # 1. VÉRIFICATION DES PRÉDICTIONS EXISTANTES
        if current_prediction_target:
            target_num = current_prediction_target['game_number']
            
            # Vérifier si ce message concerne notre prédiction
            result = await check_prediction_result(message_text, target_num)
            
            if result:
                logger.info(f"🎯 Résultat trouvé pour #{target_num}: {result}")
                await update_prediction_status(target_num, result)
                
                # Si on était en pause, on ne fait pas de nouvelle prédiction immédiatement
                if is_currently_paused():
                    logger.info("⏸️ En pause, pas de nouvelle prédiction")
                    return
        
        # 2. GESTION DES PAUSES
        if is_currently_paused():
            remaining = get_remaining_pause_time()
            logger.info(f"⏸️ Pause en cours, {remaining}s restantes")
            return
        
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
        
        # 3. LOGIQUE APRÈS PAUSE - ATTENTE DU BON MOMENT
        if pause_config.get('just_resumed'):
            pause_config['just_resumed'] = False
            save_pause_config()
            
            # Trouver le prochain numéro pair valide après le numéro actuel
            next_even = get_next_prediction_number(game_number)
            if not next_even:
                logger.info("Aucun prochain numéro pair trouvé")
                return
            
            # Vérifier si on doit attendre l'impair précédent
            target_odd = get_previous_odd_number(next_even)
            
            if game_number < target_odd:
                logger.info(f"⏳ Attente de #{target_odd} avant de prédire #{next_even} (après pause)")
                return  # On attend encore
        
        # 4. LANCER UNE NOUVELLE PRÉDICTION
        # On prédit le prochain numéro pair valide après le numéro actuel
        if game_number in VALID_EVEN_NUMBERS or game_number % 2 == 1:
            # Si on est sur un impair, le prochain pair est game_number + 1
            # Si on est sur un pair, on cherche le suivant dans la liste
            if game_number % 2 == 0 and game_number % 10 != 0 and game_number >= 6:
                # On est sur un pair valide, on prédit le suivant
                next_num = get_next_prediction_number(game_number)
            else:
                # On est sur un impair ou pair non valide, on cherche le prochain pair valide
                next_num = get_next_prediction_number(game_number)
            
            if next_num and next_num not in already_predicted_games:
                suit = get_suit_for_number(next_num)
                if suit:
                    logger.info(f"🔮 Prédiction lancée: #{next_num} -> {suit}")
                    await send_prediction(next_num, suit, game_number)
                    already_predicted_games.add(next_num)
        
    except Exception as e:
        logger.error(f"❌ Erreur process_source_message: {e}")
        import traceback
        logger.error(traceback.format_exc())

# ============================================================
# GESTION DES MESSAGES ET COMMANDES
# ============================================================

@client.on(events.NewMessage)
async def handle_new_message(event):
    logger.info(f"Message reçu de {event.sender_id}: {event.message.message}")
    
    if event.is_group or event.is_channel:
        # Analyser les messages du canal source
        if event.chat_id == get_source_channel_id():
            await process_source_message(event)
        return

    # Si c'est une commande commençant par /, ne pas traiter ici pour laisser les handlers spécifiques agir
    if event.message.message and event.message.message.startswith('/'):
        return

    user_id = event.sender_id
    user = get_user(user_id)

    # Gestion des états admin (durée)
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

    # Gestion des messages personnalisés de l'admin
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

    # Inscription
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

            # Notification admin nouvelle inscription
            await client.send_message(ADMIN_ID, f"""🆕 **NOUVELLE INSCRIPTION**

👤 **Nom:** {message_text} {user.get('nom', '')}
🆔 **ID:** `{user_id}`
📍 **Pays:** {message_text}

L'utilisateur va recevoir le lien d'essai de {get_trial_duration()} min.""")

            # Envoyer le lien d'essai immédiatement
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

    # Gestion des captures d'écran
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
# CALLBACKS VALIDATION PAIEMENT (CORRIGÉ)
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
# COMMANDES ADMIN - GESTION DES CANAUX
# ============================================================

@client.on(events.NewMessage(pattern=r'^/setchannel(\s+.+)?$'))
async def cmd_setchannel(event):
    """
    Définit les canaux.
    Usage: /setchannel type id
    Types: source, prediction, vip
    """
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
    """Affiche la configuration actuelle des canaux."""
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
    """Réinitialise tous les canaux aux valeurs par défaut."""
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

# ============================================================
# COMMANDES ADMIN - GESTION TEMPS ET UTILISATEURS
# ============================================================

@client.on(events.NewMessage(pattern=r'^/settime(\s+\d+)?(\s+.+)?$'))
async def cmd_settime(event):
    """Définit la durée d'un utilisateur."""
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
    """Arrête les prédictions automatiques."""
    if event.sender_id != ADMIN_ID: 
        return
    global predictions_enabled
    predictions_enabled = False
    await event.respond("🛑 **PRÉDICTIONS AUTOMATIQUES ARRÊTÉES**")

@client.on(events.NewMessage(pattern='/resume'))
async def cmd_resume(event):
    """Force le redémarrage et débloque les prédictions."""
    if event.sender_id != ADMIN_ID: 
        return
    global predictions_enabled, already_predicted_games, current_prediction_target
    predictions_enabled = True
    # Déblocage : on vide les verrous de sécurité
    already_predicted_games.clear()
    current_prediction_target = None
    await event.respond("🚀 **PRÉDICTIONS REDÉMARRÉES ET DÉBLOQUÉES**\n(Historique de sécurité vidé)")

@client.on(events.NewMessage(pattern=r'^/setnext (\d+) ([♥♠♦♣])$'))
async def cmd_setnext(event):
    """
    Commande pour l'administrateur : définit manuellement le prochain numéro à prédire.
    Usage: /setnext NUMERO COSTUME
    Exemple: /setnext 1234 ♥
    """
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

# ============================================================
# COMMANDES ADMIN - GESTION DES PAUSES
# ============================================================

@client.on(events.NewMessage(pattern=r'^/pausecycle(\s+.+)?$'))
async def cmd_pausecycle(event):
    """Configure le cycle de pause."""
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
        new_cycle = [int(x.strip()) * 60 for x in cycle_str.split(',')]  # Convertir en secondes
        
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
    """Affiche les infos de prédiction actuelles."""
    if event.sender_id != ADMIN_ID:
        return
    
    current_cycle = [x//60 for x in pause_config['cycle']]
    info = f"""📊 **INFO PRÉDICTION**
    
**Numéro source actuel:** {current_game_number}
**Dernier prédit:** {last_predicted_number}
**En pause:** {'Oui' if is_currently_paused() else 'Non'}
**Temps pause restant:** {get_remaining_pause_time()}s
**Compteur avant pause:** {pause_config['predictions_count']}/5
**Index pause:** {pause_config['current_index']}
**Cycle pause:** {current_cycle} min

**Prédiction en cours:** {current_prediction_target['game_number'] if current_prediction_target else 'Aucune'}
**Costume prédit:** {current_prediction_target['suit'] if current_prediction_target else 'N/A'}
"""
    await event.respond(info)

@client.on(events.NewMessage(pattern='/forcepause'))
async def cmd_forcepause(event):
    """Force une pause immédiate."""
    if event.sender_id != ADMIN_ID:
        return
    
    duration = start_pause()
    minutes = duration // 60
    await event.respond(f"⏸️ **PAUSE FORCÉE**\nDurée: {minutes} minutes")

@client.on(events.NewMessage(pattern='/resetpause'))
async def cmd_resetpause(event):
    """Réinitialise le compteur de pause."""
    if event.sender_id != ADMIN_ID:
        return
    
    reset_pause_counter()
    pause_config['is_paused'] = False
    pause_config['just_resumed'] = False
    save_pause_config()
    await event.respond("✅ **Compteur de pause réinitialisé**")

# ============================================================
# COMMANDES ADMIN - GESTION DES ESSAIS (NOUVEAU)
# ============================================================

@client.on(events.NewMessage(pattern=r'^/settrialtime(\s+\d+)?$'))
async def cmd_settrialtime(event):
    """Définit la durée de l'essai (en minutes)."""
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
    """Liste les utilisateurs en période d'essai actifs."""
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
        await event.respond(f"""🎁 **UTILISATEURS EN ESSAI** ({i+1}-{min(i+len(chunk), len(trial_users))}/{len(trial_users)})

{chunk_text}

💡 `/extendtrial ID minutes` | `/canceltrial ID` | `/userinfo ID`""")
        await asyncio.sleep(0.5)

@client.on(events.NewMessage(pattern=r'^/extendtrial (\d+) (\d+)$'))
async def cmd_extendtrial(event):
    """Prolonge l'essai d'un utilisateur."""
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
        
        # Calculer la nouvelle date d'expiration
        current_end = datetime.fromisoformat(user['trial_joined_at']) + timedelta(minutes=get_trial_duration())
        new_end = current_end + timedelta(minutes=additional_minutes)
        
        # Mettre à jour (on ajuste le trial_joined_at pour compenser)
        new_start = new_end - timedelta(minutes=get_trial_duration())
        update_user(user_id, {'trial_joined_at': new_start.isoformat()})
        
        # Annuler l'ancienne tâche d'expulsion et créer une nouvelle
        # Note: En pratique, on laisse l'ancienne tâche expirer et on vérifie à l'expulsion
        
        await event.respond(f"""✅ **ESSAI PROLONGÉ**

🆔 **ID:** `{user_id}`
👤 **Nom:** {user.get('prenom', '')} {user.get('nom', '')}
⏱️ **Temps ajouté:** {additional_minutes} minutes
📅 **Nouvelle fin:** {new_end.strftime('%d/%m/%Y %H:%M:%S')}""")
        
        # Notifier l'utilisateur
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
    """Annule l'essai et expulse immédiatement."""
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
        
        # Expulser immédiatement
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
        
        # Notifier l'utilisateur
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
# COMMANDES ADMIN - GESTION DES ABONNÉS (NOUVEAU)
# ============================================================

@client.on(events.NewMessage(pattern='/subscribers'))
async def cmd_subscribers(event):
    """Liste les utilisateurs abonnés actifs avec temps restant."""
    if event.sender_id != ADMIN_ID:
        return
    
    sub_users = []
    for user_id_str, user_info in users_data.items():
        user_id = int(user_id_str)
        if is_user_subscribed(user_id):
            remaining = format_time_remaining(user_info.get('subscription_end'))
            nom = user_info.get('prenom', '') or 'N/A'
            prenom = user_info.get('nom', '') or 'N/A'
            total_added = user_info.get('total_time_added', 0)
            sub_users.append(f"🆔 `{user_id}` | {nom} {prenom} | ⏳ {remaining} | 📊 {total_added}min")
    
    if not sub_users:
        await event.respond("📊 Aucun abonné actif.")
        return
    
    chunk_size = 50
    for i in range(0, len(sub_users), chunk_size):
        chunk = sub_users[i:i+chunk_size]
        chunk_text = '\n'.join(chunk)
        await event.respond(f"""✅ **ABONNÉS ACTIFS** ({i+1}-{min(i+len(chunk), len(sub_users))}/{len(sub_users)})

{chunk_text}

💡 `/addtime ID durée` | `/rem
