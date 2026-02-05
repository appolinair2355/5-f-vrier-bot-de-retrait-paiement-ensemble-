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
CAMP_CONFIG_FILE = "camp_config.json"
VIP_CONFIG_FILE = "vip_config.json"
CHANNELS_CONFIG_FILE = "channels_config.json"

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

# Système de Camp de Cycle
CAMP_CYCLE_DEFAULT = [2, 1, 4, 2, 3]
CAMP_MIN_DEFAULT = 6
CAMP_MAX_DEFAULT = 1436

camp_config = {
    'cycle': CAMP_CYCLE_DEFAULT.copy(),
    'min': CAMP_MIN_DEFAULT,
    'max': CAMP_MAX_DEFAULT,
    'generated_numbers': [],
    'suit_to_numbers': {},
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
# SYSTÈME DE CAMP DE CYCLE
# ============================================================

def load_camp_config():
    global camp_config
    try:
        if os.path.exists(CAMP_CONFIG_FILE):
            with open(CAMP_CONFIG_FILE, 'r', encoding='utf-8') as f:
                saved_config = json.load(f)
                camp_config.update(saved_config)
        else:
            generate_camp_numbers()
    except Exception as e:
        logger.error(f"Erreur chargement camp_config: {e}")
        generate_camp_numbers()

def save_camp_config():
    try:
        with open(CAMP_CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(camp_config, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Erreur sauvegarde camp_config: {e}")

def get_even_numbers_in_range(min_num, max_num):
    even_numbers = []
    for num in range(min_num, max_num + 1):
        if num % 2 == 0 and num % 10 != 0:
            even_numbers.append(num)
    return even_numbers

def divide_into_camps(numbers, camp_size=4):
    camps = []
    for i in range(0, len(numbers), camp_size):
        camp = numbers[i:i + camp_size]
        camps.append(camp)
    return camps

def apply_cycle_to_camps(camps, cycle):
    selected_numbers = []
    cycle_index = 0

    for camp in camps:
        num_to_select = cycle[cycle_index % len(cycle)]
        num_to_select = min(num_to_select, len(camp))

        if num_to_select > 0 and camp:
            selected = random.sample(camp, num_to_select)
            selected_numbers.extend(selected)

        cycle_index += 1

    return selected_numbers

def generate_camp_numbers():
    global camp_config

    min_num = camp_config['min']
    max_num = camp_config['max']
    cycle = camp_config['cycle']

    even_numbers = get_even_numbers_in_range(min_num, max_num)
    camps = divide_into_camps(even_numbers, 4)
    selected_numbers = apply_cycle_to_camps(camps, cycle)

    suit_to_numbers = {}
    numbers_per_suit = len(selected_numbers) // 4
    remainder = len(selected_numbers) % 4

    start_idx = 0
    for i, suit in enumerate(ALL_SUITS):
        count = numbers_per_suit + (1 if i < remainder else 0)
        suit_to_numbers[suit] = selected_numbers[start_idx:start_idx + count]
        start_idx += count

    camp_config['generated_numbers'] = selected_numbers
    camp_config['suit_to_numbers'] = suit_to_numbers
    camp_config['camps'] = [camp for camp in camps]

    save_camp_config()
    return selected_numbers

def get_suit_for_game_number(game_number):
    for suit, numbers in camp_config['suit_to_numbers'].items():
        if game_number in numbers:
            return suit
    return None

def is_one_part_before_camp(current_number):
    if current_number % 2 == 0:
        return None
    next_camp_num = current_number + 1
    if next_camp_num in camp_config['generated_numbers']:
        return next_camp_num
    return None

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
            'subscription_end': None,
            'subscription_type': None,
            'pending_payment': False,
            'awaiting_screenshot': False,
            'awaiting_amount': False,
            'vip_expires_at': None,
            'vip_duration_minutes': None,
            'vip_joined_at': None
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
    if user.get('trial_used') or not user.get('trial_started'):
        return False
    try:
        trial_start = datetime.fromisoformat(user['trial_started'])
        trial_end = trial_start + timedelta(minutes=60)
        return datetime.now() < trial_end
    except:
        return False

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
# GESTION DU CANAL VIP
# ============================================================

async def add_user_to_vip(user_id: int, duration_minutes: int):
    """Ajoute un utilisateur au canal VIP et programme son expulsion."""
    try:
        now = datetime.now()
        expires_at = now + timedelta(minutes=duration_minutes)

        update_user(user_id, {
            'vip_joined_at': now.isoformat(),
            'vip_expires_at': expires_at.isoformat(),
            'vip_duration_minutes': duration_minutes
        })

        # Envoyer le lien à l'utilisateur (sera supprimé après 2 min)
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
        await client.send_message(ADMIN_ID, f"""✅ **UTILISATEUR AJOUTÉ AU CANAL VIP**

👤 **Nom:** {user.get('prenom', '')} {user.get('nom', '')}
🆔 **ID:** `{user_id}`
⏳ **Temps restant:** {time_str}
📅 **Expire le:** {expires_at.strftime('%d/%m/%Y %H:%M:%S')}

🔗 Lien envoyé (sera supprimé dans 2 min)
👤 Utilisateur: @{user.get('username', 'N/A')}""")

        # Lancer l'expulsion automatique
        asyncio.create_task(auto_kick_user(user_id, duration_minutes * 60))

        logger.info(f"Utilisateur {user_id} ajouté au canal VIP for {duration_minutes} minutes")
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
            # Tenter de récupérer l'entité via un dialogue si possible
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
            'status': 'expired'
        })

        # Notifier l'utilisateur
        try:
            await client.send_message(user_id, """❌ **VOTRE TEMPS D'ACCÈS EST ÉCOULÉ**

Vous avez été retiré du canal VIP.

💳 Pour renouveler votre accès, utilisez la commande /payer

Merci d'avoir utilisé nos services!""")
        except:
            pass

        # Notifier l'admin
        await client.send_message(ADMIN_ID, f"""🚫 **UTILISATEUR RETIRÉ (TEMPS ÉCOULÉ)**

🆔 **ID:** `{user_id}`
👤 **Nom:** {user.get('prenom', '')} {user.get('nom', '')}

L'utilisateur a été expulsé du canal VIP.""")

        logger.info(f"Utilisateur {user_id} expulsé du canal VIP")

    except Exception as e:
        logger.error(f"Erreur expulsion utilisateur {user_id}: {e}")

# ============================================================
# SYSTÈME DE PRÉDICTION (ENVOYÉ DANS LE CANAL DE PRÉDICTION)
# ============================================================

predictions_enabled = True

async def send_prediction_to_channel(target_game: int, predicted_suit: str, base_game: int, 
                                     rattrapage=0, original_game=None):
    """Envoie la prédiction dans le canal de prédiction."""
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
        pending_predictions[target_game] = {
            'message_id': sent_msg.id,
            'channel_id': prediction_channel_id,
            'suit': predicted_suit,
            'base_game': base_game,
            'status': '⌛',
            'check_count': 0,
            'rattrapage': 0,
            'created_at': datetime.now().isoformat()
        }
        return True
    except Exception as e:
        logger.error(f"❌ Erreur envoi prédiction: {e}")
        return False

async def update_prediction_in_channel(game_number: int, new_status: str):
    """Met à jour la prédiction dans le canal de prédiction."""
    try:
        if game_number not in pending_predictions:
            return False

        pred = pending_predictions[game_number]
        suit = pred['suit']
        channel_id = pred['channel_id']
        message_id = pred['message_id']

        if new_status == "❌":
            status_text = "❌ PERDU"
        elif new_status.startswith("✅"):
            status_text = f"✅ GAGNÉ ({new_status})"
        else:
            status_text = f"{new_status}"

        updated_msg = f"""🎰 **PRÉDICTION #{game_number}**
🎯 Couleur: {SUIT_DISPLAY.get(suit, suit)}
📊 Statut: {status_text}"""

        await client.edit_message(channel_id, message_id, updated_msg)
        pred['status'] = new_status
        # ... suite de la logique de stats (déjà présente dans le code original)
        return True
    except Exception:
        return False

        pred['status'] = new_status

        # Mise à jour des statistiques
        if new_status in ['✅0️⃣', '✅1️⃣', '✅2️⃣', '✅3️⃣']:
            stats_bilan['total'] += 1
            stats_bilan['wins'] += 1
            stats_bilan['win_details'][new_status] = (stats_bilan['win_details'].get(new_status, 0) + 1)
            if game_number in pending_predictions:
                del pending_predictions[game_number]

        elif new_status == '❌':
            stats_bilan['total'] += 1
            stats_bilan['losses'] += 1
            stats_bilan['loss_details']['❌'] += 1
            if game_number in pending_predictions:
                del pending_predictions[game_number]

        return True

    except Exception as e:
        logger.error(f"Erreur mise à jour prédiction: {e}")
        return False

# --- Fonctions d'Analyse ---

def extract_game_number(message: str):
    match = re.search(r"#N\s*(\d+)", message, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None

def extract_parentheses_groups(message: str):
    return re.findall(r"\(([^)]*)\)", message)

def normalize_suits(group_str: str) -> str:
    normalized = group_str.replace('❤️', '♥').replace('❤', '♥').replace('♥️', '♥')
    normalized = normalized.replace('♠️', '♠').replace('♦️', '♦').replace('♣️', '♣')
    return normalized

def has_suit_in_group(group_str: str, target_suit: str) -> bool:
    normalized = normalize_suits(group_str)
    target_normalized = normalize_suits(target_suit)
    for suit in ALL_SUITS:
        if suit in normalized and suit == target_normalized:
            return True
    return False

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
            
            if duration_minutes is None:
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

            success_msg = f"""🎉 **FÉLICITATIONS!** 🎉

✅ Votre compte est ACTIVÉ!
⏰ **60 MINUTES D'ESSAI GRATUIT**

🚀 **Comment ça marche?**
1️⃣ Je surveille le canal source
2️⃣ Je détecte les numéros à 1 part
3️⃣ Je publie les prédictions dans le canal de prédiction

⚠️ **IMPORTANT:** Restez dans ce chat!

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
# CALLBACKS VALIDATION PAIEMENT
# ============================================================

@client.on(events.CallbackQuery(data=re.compile(b'validate_payment_(\d+)')))
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

@client.on(events.CallbackQuery(data=re.compile(b'reject_payment_(\d+)')))
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

        if duration_minutes is None:
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
    if event.sender_id != ADMIN_ID: return
    global predictions_enabled
    predictions_enabled = False
    await event.respond("🛑 **PRÉDICTIONS AUTOMATIQUES ARRÊTÉES**")

@client.on(events.NewMessage(pattern='/resume'))
async def cmd_resume(event):
    """Force le redémarrage et débloque les prédictions."""
    if event.sender_id != ADMIN_ID: return
    global predictions_enabled, already_predicted_games, pending_predictions
    predictions_enabled = True
    # Déblocage : on vide les verrous de sécurité
    already_predicted_games.clear()
    pending_predictions.clear()
    await event.respond("🚀 **PRÉDICTIONS REDÉMARRÉES ET DÉBLOQUÉES**\n(Historique de sécurité vidé)")

@client.on(events.NewMessage(pattern=r'^/setnext (\d+) ([♥♠♦♣]) (\d+)$'))
async def cmd_setnext(event):
    """
    Commande pour l'administrateur : définit manuellement le prochain numéro, 
    le costume à prédire et le dernier numéro reçu.
    Usage: /setnext PROCHAIN_NUMERO COSTUME DERNIER_NUMERO_SOURCE
    Exemple: /setnext 1234 ♥ 1233
    """
    if event.is_group or event.is_channel: 
        return

    if event.sender_id != ADMIN_ID:
        await event.respond("❌ Commande réservée à l'administrateur.")
        return

    try:
        next_num = int(event.pattern_match.group(1))
        suit = event.pattern_match.group(2)
        last_source = int(event.pattern_match.group(3))

        global current_game_number, last_source_game_number
        current_game_number = last_source
        last_source_game_number = last_source

        # On envoie la prédiction immédiatement pour le prochain numéro
        await send_prediction_to_channel(next_num, suit, last_source)
        already_predicted_games.add(last_source)

        await event.respond(f"""✅ **CONFIGURATION MANUELLE RÉUSSIE**

• Dernier numéro source: `{last_source}`
• Prédiction envoyée pour: `{next_num}`
• Costume: {SUIT_DISPLAY.get(suit, suit)}""")

    except Exception as e:
        await event.respond(f"❌ Erreur: {e}")

@client.on(events.NewMessage(pattern=r'^/camp(\s+.+)?$'))
async def cmd_camp(event):
    """Configure le camp de cycle."""
    if event.is_group or event.is_channel: 
        return

    if event.sender_id != ADMIN_ID:
        await event.respond("❌ Commande réservée à l'administrateur.")
        return

    message_text = event.message.message.strip()
    parts = message_text.split()

    if len(parts) == 1:
        current_cycle = camp_config['cycle']
        current_min = camp_config['min']
        current_max = camp_config['max']
        num_count = len(camp_config['generated_numbers'])

        suit_mapping_info = ""
        for suit in ALL_SUITS:
            numbers = camp_config['suit_to_numbers'].get(suit, [])
            suit_mapping_info += f"{SUIT_DISPLAY.get(suit, suit)}: {len(numbers)} numéros\n"

        await event.respond(f"""🏕️ **CONFIGURATION CAMP DE CYCLE**

**Paramètres:**
• Cycle: `{current_cycle}`
• Min: `{current_min}`
• Max: `{current_max}`
• Numéros: `{num_count}`

**Distribution:**
{suit_mapping_info}

**Modifier:**
`/camp cycle min max`
Ex: `/camp 2,1,4,2,3 6 1436`""")
        return

    try:
        cycle_str = parts[1]
        new_cycle = [int(x.strip()) for x in cycle_str.split(',')]
        new_min = int(parts[2]) if len(parts) >= 3 else camp_config['min']
        new_max = int(parts[3]) if len(parts) >= 4 else camp_config['max']

        if not new_cycle or any(x <= 0 for x in new_cycle):
            await event.respond("❌ Le cycle doit contenir des nombres positifs.")
            return

        camp_config['cycle'] = new_cycle
        camp_config['min'] = new_min
        camp_config['max'] = new_max

        generate_camp_numbers()

        await event.respond(f"""✅ **CAMP MIS À JOUR!**

• Cycle: `{new_cycle}`
• Min: `{new_min}`
• Max: `{new_max}`
• Numéros: `{len(camp_config['generated_numbers'])}`""")

    except Exception as e:
        await event.respond(f"❌ Erreur: {e}")

@client.on(events.NewMessage(pattern='/users'))
async def cmd_users(event):
    """Liste les utilisateurs."""
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

💡 `/settime ID DURÉE`""")
        await asyncio.sleep(0.5)

@client.on(events.NewMessage(pattern=r'^/msg (\d+)$'))
async def cmd_msg(event):
    """Envoie un message à un utilisateur."""
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
    """Voir le statut."""
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

    if user.get('vip_expires_at'):
        vip_remaining = format_time_remaining(user['vip_expires_at'])
        response += f"\n🔑 **Accès VIP:** {vip_remaining}"

    if is_trial_active(user_id):
        trial_start = datetime.fromisoformat(user['trial_started'])
        trial_end = trial_start + timedelta(minutes=60)
        remaining = (trial_end - datetime.now()).seconds // 60
        response += f"\n🎁 **Essai:** {remaining} min"

    response += "\n\n💡 `/payer` pour renouveler"

    await event.respond(response)

@client.on(events.NewMessage(pattern='/bilan'))
async def cmd_bilan(event):
    """Statistiques."""
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
• 3ème: {stats_bilan['win_details'].get('✅2️⃣', 0)}""")

@client.on(events.NewMessage(pattern='/reset'))
async def cmd_reset_all(event):
    """Reset total."""
    if event.is_group or event.is_channel: 
        return
    if event.sender_id != ADMIN_ID:
        await event.respond("❌ Admin uniquement")
        return

    global users_data, pending_predictions, processed_messages
    global current_game_number, last_source_game_number, stats_bilan
    global already_predicted_games, pending_payments

    users_data = {}
    save_users_data()
    pending_predictions.clear()
    processed_messages.clear()
    already_predicted_games.clear()
    pending_payments.clear()

    current_game_number = 0
    last_source_game_number = 0

    stats_bilan = {
        'total': 0, 'wins': 0, 'losses': 0,
        'win_details': {'✅0️⃣': 0, '✅1️⃣': 0, '✅2️⃣': 0},
        'loss_details': {'❌': 0}
    }

    await event.respond("🚨 **RESET EFFECTUÉ**")

@client.on(events.NewMessage(pattern='/help'))
async def cmd_help(event):
    """Aide."""
    if event.is_group or event.is_channel: 
        return

    admin_cmds = ""
    if event.sender_id == ADMIN_ID:
        admin_cmds = """
**Admin:**
/users - Liste des utilisateurs
/settime ID DURÉE - Donner accès VIP
/setchannel TYPE ID [LIEN] - Configurer canaux
/channels - Voir config canaux
/camp - Configurer Camp de Cycle
/bilan - Statistiques
/reset - Tout réinitialiser
/msg ID - Envoyer message privé
"""

    await event.respond(f"""📖 **AIDE**

**Utilisateur:**
/start - Inscription
/status - Voir temps restant
/payer - Renouveler
/help - Cette aide
{admin_cmds}
**Système:**
• Prédictions dans le canal de prédiction
• Détection à 1 part automatique
• Lien VIP supprimé après 2 min

**Support:** @Kouamappoloak""")

@client.on(events.NewMessage(pattern='/payer'))
async def cmd_payer(event):
    """Paiement."""
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
# SERVEUR WEB ET DÉMARRAGE
# ============================================================

async def index(request):
    html = f"""<!DOCTYPE html>
<html>
<head>
    <title>Bot Baccarat - Camp de Cycle</title>
    <style>
        body {{ font-family: Arial, sans-serif; background: linear-gradient(135deg, #1e3c72 0%, #2a5298 100%); color: white; text-align: center; padding: 50px; }}
        h1 {{ font-size: 3em; margin-bottom: 20px; }}
        .status {{ background: rgba(255,255,255,0.1); padding: 30px; border-radius: 15px; display: inline-block; margin: 20px; }}
        .number {{ font-size: 2.5em; font-weight: bold; color: #ffd700; }}
        .label {{ font-size: 1.2em; opacity: 0.9; }}
    </style>
</head>
<body>
    <h1>🎰 Bot Baccarat - Camp de Cycle</h1>
    <div class="status">
        <div class="label">Jeu Actuel</div>
        <div class="number">#{current_game_number}</div>
    </div>
    <div class="status">
        <div class="label">Utilisateurs</div>
        <div class="number">{len(users_data)}</div>
    </div>
    <div class="status">
        <div class="label">Prédictions</div>
        <div class="number">{len(pending_predictions)}</div>
    </div>
    <div class="status">
        <div class="label">Canal Source</div>
        <div class="number">{get_source_channel_id()}</div>
    </div>
    <div class="status">
        <div class="label">Canal Prédiction</div>
        <div class="number">{get_prediction_channel_id()}</div>
    </div>
    <p style="margin-top: 40px;">✅ Système opérationnel | Lien VIP auto-supprimé | Prédictions dans canal dédié</p>
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
        # Placeholder for daily reset logic if needed
        await asyncio.sleep(3600)

@client.on(events.NewMessage(pattern=r'^/start$'))
async def cmd_start(event):
    """Commande de démarrage / inscription."""
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

🚀 **Système Camp de Cycle**
• Détection automatique à 1 part
• Prédictions dans canal dédié
• Gestion VIP automatisée

📝 **Étape 1/3: Quel est votre nom de famille?**"""

    await event.respond(welcome_msg)

async def process_source_message(event):
    """Traite les messages reçus du canal source."""
    global current_game_number, last_source_game_number
    
    try:
        message_text = event.message.message
        logger.info(f"Analyse message source: {message_text[:50]}...")
        game_number = extract_game_number(message_text)
        
        if game_number is None:
            logger.info("Aucun numéro de jeu détecté.")
            return

        current_game_number = game_number
        last_source_game_number = game_number
        
        if game_number in already_predicted_games:
            logger.info(f"Jeu #{game_number} déjà traité.")
            return
            
        # Logique de détection "1 part" ou camp
        suit = get_suit_for_game_number(game_number)
        
        # Détection spécifique "1 part" (ex: message contenant ⌛ ou un indicateur de camp)
        # On peut aussi se baser sur le fait que le numéro est dans notre camp_config
        if suit:
            logger.info(f"🎯 Camp détecté pour #{game_number}! Prédiction pour #{game_number + 1}: {suit}")
            await send_prediction_to_channel(game_number + 1, suit, game_number)
            already_predicted_games.add(game_number)
        else:
            # Vérifier si c'est "une part avant le camp"
            next_camp_num = is_one_part_before_camp(game_number)
            if next_camp_num:
                next_suit = get_suit_for_game_number(next_camp_num)
                logger.info(f"🕒 Une part avant le camp ({next_camp_num}) détectée!")
                await send_prediction_to_channel(next_camp_num, next_suit, game_number)
                already_predicted_games.add(game_number)

    except Exception as e:
        logger.error(f"Erreur process_source_message: {e}")

async def main():
    """Point d'entrée principal."""
    while True:
        try:
            # Chargement des configurations
            load_channels_config()
            load_vip_config()
            load_camp_config()
            load_users_data()
            
            # Démarrer le serveur web
            await start_web_server()
            
            # Démarrer le client Telegram
            await client.start(bot_token=BOT_TOKEN)
            
            # Forcer la récupération des dialogues pour peupler le cache d'entités
            logger.info("Récupération des dialogues pour le cache d'entités...")
            try:
                await client.get_entity(get_source_channel_id())
                await client.get_entity(get_prediction_channel_id())
                await client.get_entity(get_vip_channel_id())
            except Exception as e:
                logger.warning(f"Note: Certains canaux ne sont pas encore accessibles (normal pour un bot): {e}")
            
            # S'assurer que le bot est bien connecté en tant que bot
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
