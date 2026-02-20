import os
import asyncio
import re
import logging
import sys
import json
from datetime import datetime, timedelta, timezone
from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession
from telethon.tl.functions.channels import EditBannedRequest
from telethon.tl.types import ChatBannedRights
from aiohttp import web
from config import (
    API_ID, API_HASH, BOT_TOKEN, ADMIN_ID,
    PORT, SUIT_DISPLAY
)

USERS_FILE = "users_data.json"
PAUSE_CONFIG_FILE = "pause_config.json"
CHANNELS_CONFIG_FILE = "channels_config.json"

# Configuration par défaut des canaux
DEFAULT_SOURCE_CHANNEL_ID = -1002682552255
DEFAULT_PREDICTION_CHANNEL_ID = -1003329818758

# --- Configuration Logging ---
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
}

# Cycle de pause par défaut: 3min, 5min, 4min
DEFAULT_PAUSE_CYCLE = [180, 300, 240]
pause_config = {
    'cycle': DEFAULT_PAUSE_CYCLE.copy(),
    'current_index': 0,
    'predictions_count': 0,
    'is_paused': False,
    'pause_end_time': None,
    'just_resumed': False
}

# État global
users_data = {}
current_game_number = 0
last_source_game_number = 0
last_predicted_number = None
predictions_enabled = True
already_predicted_games = set()

# État de vérification
verification_state = {
    'predicted_number': None,
    'predicted_suit': None,
    'current_check': 0,
    'message_id': None,
    'channel_id': None,
    'status': None,
    'base_game': None
}

SUIT_CYCLE = ['♥', '♦', '♣', '♠', '♦', '♥', '♠', '♣']

stats_bilan = {
    'total': 0, 'wins': 0, 'losses': 0,
    'win_details': {'✅0️⃣': 0, '✅1️⃣': 0, '✅2️⃣': 0, '✅3️⃣': 0},
    'loss_details': {'❌': 0}
}

# Variables pour le reset automatique
last_prediction_time = None
auto_reset_task = None

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
    global channels_config, pause_config, users_data
    channels_config.update(load_json(CHANNELS_CONFIG_FILE, channels_config))
    pause_config.update(load_json(PAUSE_CONFIG_FILE, pause_config))
    users_data.update(load_json(USERS_FILE, {}))
    logger.info("Configurations chargées")

def save_all_configs():
    save_json(CHANNELS_CONFIG_FILE, channels_config)
    save_json(PAUSE_CONFIG_FILE, pause_config)
    save_json(USERS_FILE, users_data)

# ============================================================
# GESTION NUMÉROS ET COSTUMES
# ============================================================

def get_valid_even_numbers():
    """Génère la liste des pairs valides: 6-1436, pairs, ne finissant pas par 0"""
    valid = []
    for num in range(6, 1437):
        if num % 2 == 0 and num % 10 != 0:
            valid.append(num)
    return valid

VALID_EVEN_NUMBERS = get_valid_even_numbers()
logger.info(f"📊 Pairs valides: {len(VALID_EVEN_NUMBERS)} numéros")

def get_suit_for_number(number):
    """Retourne le costume pour un numéro pair valide"""
    if number not in VALID_EVEN_NUMBERS:
        logger.error(f"❌ Numéro {number} non valide")
        return None
    idx = VALID_EVEN_NUMBERS.index(number) % len(SUIT_CYCLE)
    return SUIT_CYCLE[idx]

def is_trigger_number(number):
    """Déclencheur: impair finissant par 1,3,5,7 ET suivant est pair valide"""
    if number % 2 == 0:
        return False

    last_digit = number % 10
    if last_digit not in [1, 3, 5, 7]:
        return False

    next_num = number + 1
    is_valid = next_num in VALID_EVEN_NUMBERS

    if is_valid:
        logger.info(f"🔥 DÉCLENCHEUR #{number} (suivant: #{next_num})")

    return is_valid

def get_trigger_target(number):
    """Retourne le numéro pair à prédire"""
    if not is_trigger_number(number):
        return None
    return number + 1

# ============================================================
# GESTION CANAUX
# ============================================================

def get_source_channel_id():
    return channels_config.get('source_channel_id', DEFAULT_SOURCE_CHANNEL_ID)

def get_prediction_channel_id():
    return channels_config.get('prediction_channel_id', DEFAULT_PREDICTION_CHANNEL_ID)

def set_channels(source_id=None, prediction_id=None):
    if source_id:
        channels_config['source_channel_id'] = source_id
    if prediction_id:
        channels_config['prediction_channel_id'] = prediction_id
    save_json(CHANNELS_CONFIG_FILE, channels_config)
    logger.info(f"Canaux mis à jour")

# ============================================================
# SYSTÈME DE PRÉDICTION ET VÉRIFICATION
# ============================================================

async def send_prediction(target_game: int, predicted_suit: str, base_game: int):
    """Envoie une prédiction au canal configuré"""
    global verification_state, last_predicted_number, last_prediction_time

    if not predictions_enabled:
        logger.warning("⛔ Prédictions désactivées")
        return False

    if verification_state['predicted_number'] is not None:
        logger.error(f"⛔ BLOQUÉ: Prédiction #{verification_state['predicted_number']} en cours!")
        return False

    try:
        prediction_channel_id = get_prediction_channel_id()
        entity = await client.get_input_entity(prediction_channel_id)

        prediction_text = f"""🎰 **PRÉDICTION #{target_game}**
🎯 Couleur: {SUIT_DISPLAY.get(predicted_suit, predicted_suit)}
⏳ Statut: EN ATTENTE DU RÉSULTAT..."""

        sent_msg = await client.send_message(entity, prediction_text)

        verification_state = {
            'predicted_number': target_game,
            'predicted_suit': predicted_suit,
            'current_check': 0,
            'message_id': sent_msg.id,
            'channel_id': prediction_channel_id,
            'status': 'pending',
            'base_game': base_game
        }

        last_predicted_number = target_game
        last_prediction_time = datetime.now()

        logger.info(f"🚀 PRÉDICTION #{target_game} ({predicted_suit}) LANCÉE")
        logger.info(f"🔍 Attente vérification: #{target_game} (check 0/3)")

        return True

    except Exception as e:
        logger.error(f"❌ Erreur envoi prédiction: {e}")
        return False

async def update_prediction_status(status: str):
    """Met à jour le statut de la prédiction"""
    global verification_state, stats_bilan, last_prediction_time

    if verification_state['predicted_number'] is None:
        logger.error("❌ Aucune prédiction à mettre à jour")
        return False

    try:
        predicted_num = verification_state['predicted_number']
        predicted_suit = verification_state['predicted_suit']

        if status == "❌":
            status_text = "❌ PERDU"
        else:
            status_text = f"{status} GAGNÉ"

        updated_text = f"""🎰 **PRÉDICTION #{predicted_num}**
🎯 Couleur: {SUIT_DISPLAY.get(predicted_suit, predicted_suit)}
📊 Statut: {status_text}"""

        await client.edit_message(
            verification_state['channel_id'],
            verification_state['message_id'],
            updated_text
        )

        if status in ['✅0️⃣', '✅1️⃣', '✅2️⃣', '✅3️⃣']:
            stats_bilan['total'] += 1
            stats_bilan['wins'] += 1
            stats_bilan['win_details'][status] = stats_bilan['win_details'].get(status, 0) + 1
            logger.info(f"🎉 #{predicted_num} GAGNÉ ({status})")
        elif status == '❌':
            stats_bilan['total'] += 1
            stats_bilan['losses'] += 1
            logger.info(f"💔 #{predicted_num} PERDU")

        logger.info(f"🔓 SYSTÈME LIBÉRÉ - Nouvelle prédiction possible")

        verification_state = {
            'predicted_number': None, 'predicted_suit': None,
            'current_check': 0, 'message_id': None,
            'channel_id': None, 'status': None, 'base_game': None
        }

        last_prediction_time = datetime.now()

        return True

    except Exception as e:
        logger.error(f"❌ Erreur mise à jour statut: {e}")
        return False

# ============================================================
# ANALYSE MESSAGES SOURCE
# ============================================================

def extract_game_number(message: str) -> int:
    """Extrait le numéro de jeu du message (supporte #N, #R, #X, etc.)"""
    match = re.search(r"#N\s*(\d+)", message, re.IGNORECASE)
    if match:
        return int(match.group(1))

    patterns = [
        r"^#(\d+)",
        r"N\s*(\d+)",
        r"Numéro\s*(\d+)",
        r"Game\s*(\d+)"
    ]

    for pattern in patterns:
        match = re.search(pattern, message, re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None

def extract_suits_from_first_group(message_text: str) -> list:
    """Extrait les costumes du PREMIER groupe de parenthèses"""
    matches = re.findall(r"\(([^)]+)\)", message_text)
    if not matches:
        return []

    first_group = matches[0]

    normalized = first_group.replace('❤️', '♥').replace('❤', '♥')
    normalized = normalized.replace('♠️', '♠').replace('♦️', '♦').replace('♣️', '♣')
    normalized = normalized.replace('♥️', '♥')

    suits = []
    for suit in ['♥', '♠', '♦', '♣']:
        if suit in normalized:
            suits.append(suit)

    logger.debug(f"Costumes trouvés dans premier groupe '{first_group}': {suits}")
    return suits

def is_message_editing(message_text: str) -> bool:
    """Vérifie si le message est en cours d'édition (commence par ⏰)"""
    return message_text.strip().startswith('⏰')

def is_message_finalized(message_text: str) -> bool:
    """Vérifie si le message est finalisé (contient ✅ ou 🔰)"""
    return '✅' in message_text or '🔰' in message_text

async def process_verification_step(game_number: int, message_text: str):
    """Traite UNE étape de vérification"""
    global verification_state

    if verification_state['predicted_number'] is None:
        return

    predicted_num = verification_state['predicted_number']
    predicted_suit = verification_state['predicted_suit']
    current_check = verification_state['current_check']

    expected_number = predicted_num + current_check
    if game_number != expected_number:
        logger.warning(f"⚠️ Reçu #{game_number} != attendu #{expected_number}")
        return

    suits = extract_suits_from_first_group(message_text)
    logger.info(f"🔍 Vérification #{game_number}: premier groupe contient {suits}, attendu {predicted_suit}")

    if predicted_suit in suits:
        status = f"✅{current_check}️⃣"
        logger.info(f"🎉 GAGNÉ! Costume {predicted_suit} trouvé dans premier groupe au check {current_check}")
        await update_prediction_status(status)
        return

    if current_check < 3:
        verification_state['current_check'] += 1
        next_num = predicted_num + verification_state['current_check']
        logger.info(f"❌ Check {current_check} échoué sur #{game_number}, prochain: #{next_num}")
    else:
        logger.info(f"💔 PERDU après 4 vérifications (jusqu'à #{game_number})")
        await update_prediction_status("❌")

async def check_and_launch_prediction(game_number: int):
    """Vérifie et lance une prédiction avec CYCLE DE PAUSE"""
    global pause_config

    if verification_state['predicted_number'] is not None:
        logger.warning(f"⛔ BLOQUÉ: Prédiction #{verification_state['predicted_number']} en attente de vérification. Déclencheur #{game_number} ignoré.")
        return

    if pause_config['is_paused']:
        try:
            end_time = datetime.fromisoformat(pause_config['pause_end_time'])
            if datetime.now() < end_time:
                remaining = int((end_time - datetime.now()).total_seconds())
                logger.info(f"⏸️ Pause active: {remaining}s restantes")
                return
            pause_config['is_paused'] = False
            pause_config['just_resumed'] = True
            save_json(PAUSE_CONFIG_FILE, pause_config)
            logger.info("🔄 Pause terminée, reprise")
        except:
            pause_config['is_paused'] = False

    if not is_trigger_number(game_number):
        return

    target_num = get_trigger_target(game_number)
    if not target_num or target_num in already_predicted_games:
        return

    pause_config['predictions_count'] += 1
    current_count = pause_config['predictions_count']

    logger.info(f"📊 Prédiction {current_count}/5 avant pause")

    if current_count >= 5:
        cycle = pause_config['cycle']
        idx = pause_config['current_index'] % len(cycle)
        duration = cycle[idx]

        pause_config['is_paused'] = True
        pause_config['pause_end_time'] = (datetime.now() + timedelta(seconds=duration)).isoformat()
        pause_config['current_index'] += 1
        save_json(PAUSE_CONFIG_FILE, pause_config)

        minutes = duration // 60

        logger.info(f"⏸️ PAUSE: {minutes}min")

        try:
            await client.send_message(
                get_prediction_channel_id(),
                f"⏸️ **PAUSE**\n⏱️ {minutes} minutes..."
            )
        except Exception as e:
            logger.error(f"Erreur envoi message pause: {e}")

        pause_config['predictions_count'] = 0
        save_json(PAUSE_CONFIG_FILE, pause_config)

        return

    suit = get_suit_for_number(target_num)
    if suit:
        success = await send_prediction(target_num, suit, game_number)
        if success:
            already_predicted_games.add(target_num)
            logger.info(f"✅ Prédiction #{target_num} lancée ({current_count}/5)")

async def process_source_message(event, is_edit: bool = False):
    """Traite les messages du canal source"""
    global current_game_number, last_source_game_number

    try:
        message_text = event.message.message
        game_number = extract_game_number(message_text)

        if game_number is None:
            return

        is_editing = is_message_editing(message_text)
        is_finalized = is_message_finalized(message_text)

        log_type = "ÉDITÉ" if is_edit else "NOUVEAU"
        log_status = "⏰" if is_editing else ("✅" if is_finalized else "📝")
        logger.info(f"📩 {log_status} {log_type}: #{game_number}")

        if verification_state['predicted_number'] is not None:
            predicted_num = verification_state['predicted_number']
            current_check = verification_state['current_check']
            expected_number = predicted_num + current_check

            if is_editing and game_number == expected_number:
                logger.info(f"⏳ Message #{game_number} en édition, attente finalisation (✅/🔰)")
                return

            if game_number == expected_number:
                if is_finalized or not is_editing:
                    logger.info(f"✅ Numéro #{game_number} finalisé/disponible, vérification...")
                    await process_verification_step(game_number, message_text)

                    if verification_state['predicted_number'] is not None:
                        logger.info(f"⏳ Prédiction #{verification_state['predicted_number']} toujours en cours")
                        return
                    else:
                        logger.info("✅ Vérification terminée, système libre")
                else:
                    logger.info(f"⏳ Attente finalisation pour #{game_number}")
            else:
                logger.info(f"⏭️ Attente #{expected_number}, reçu #{game_number}")

            return

        await check_and_launch_prediction(game_number)

        current_game_number = game_number
        last_source_game_number = game_number

    except Exception as e:
        logger.error(f"❌ Erreur traitement message: {e}")
        import traceback
        logger.error(traceback.format_exc())

# ============================================================
# RESET AUTOMATIQUE
# ============================================================

async def auto_reset_monitor():
    """Surveille et effectue un reset automatique si nécessaire"""
    global verification_state, last_prediction_time, predictions_enabled, already_predicted_games, stats_bilan

    while True:
        await asyncio.sleep(60)  # Vérifier toutes les minutes

        try:
            now = datetime.now()
            should_reset = False
            reset_reason = ""

            # Vérifier si une prédiction est bloquée (en cours depuis trop longtemps)
            if verification_state['predicted_number'] is not None:
                # Si prédiction en cours depuis plus de 20 minutes
                if last_prediction_time and (now - last_prediction_time).total_seconds() > 1200:
                    should_reset = True
                    reset_reason = f"Prédiction #{verification_state['predicted_number']} bloquée depuis 20+ min"

            # Vérifier si aucune prédiction depuis 20 minutes
            elif last_prediction_time and (now - last_prediction_time).total_seconds() > 1200:
                should_reset = True
                reset_reason = "Aucune prédiction depuis 20+ min"

            # Si le bot vient de démarrer et pas encore de prédiction, initialiser le timer
            elif last_prediction_time is None:
                last_prediction_time = now

            if should_reset:
                logger.warning(f"🚨 RESET AUTOMATIQUE DÉCLENCHÉ: {reset_reason}")

                old_pred = verification_state['predicted_number']

                # Effectuer le reset comme la commande /reset
                verification_state = {
                    'predicted_number': None, 'predicted_suit': None,
                    'current_check': 0, 'message_id': None,
                    'channel_id': None, 'status': None, 'base_game': None
                }

                already_predicted_games.clear()
                predictions_enabled = True  # Réactiver les prédictions
                last_prediction_time = now  # Réinitialiser le timer

                # Notifier l'admin
                try:
                    await client.send_message(ADMIN_ID, f"""🚨 **RESET AUTOMATIQUE EFFECTUÉ**

**Raison:** {reset_reason}

✅ Système réinitialisé et prêt
🔄 Les prédictions reprennent normalement""")
                except Exception as e:
                    logger.error(f"Erreur notification admin: {e}")

                logger.info("✅ Reset automatique terminé - Système libéré")

        except Exception as e:
            logger.error(f"❌ Erreur dans le moniteur de reset: {e}")

# ============================================================
# COMMANDES ADMIN
# ============================================================

@client.on(events.NewMessage(pattern='/start'))
async def cmd_start(event):
    if event.is_group or event.is_channel:
        return

    user_id = event.sender_id

    if user_id == ADMIN_ID:
        await event.respond("""👑 **ADMINISTRATEUR**

Commandes:
/stop /resume - Contrôle prédictions
/forcestop - Débloquer système
/predictinfo - Statut système
/clearverif - Débloquer manuellement
/setchannel - Canaux
/pausecycle - Cycle pause
/bilan - Stats
/reset - Reset stats
/help - Aide""")
        return

    await event.respond("""👋 **Bot Baccarat - Prédictions Automatiques**

🎰 Système de prédictions automatiques activé

💡 /help pour plus d'informations""")

@client.on(events.NewMessage(pattern='/help'))
async def cmd_help(event):
    if event.is_group or event.is_channel:
        return

    user_id = event.sender_id

    if user_id == ADMIN_ID:
        await event.respond("""📖 **AIDE ADMINISTRATEUR**

**Contrôle:**
/stop - Arrêter prédictions
/resume - Reprendre prédictions  
/forcestop - Forcer arrêt immédiat

**Monitoring:**
/predictinfo - Statut système prédiction
/clearverif - Effacer vérification bloquée

**Configuration:**
/setchannel source ID - Canal source
/setchannel prediction ID - Canal prédiction  
/pausecycle - Voir/modifier cycle pause

**Statistiques:**
/bilan - Statistiques prédictions
/reset - Reset stats

**Support:** @Kouamappoloak""")
    else:
        await event.respond("""📖 **AIDE UTILISATEUR**

/start - Voir statut
/help - Cette aide

Le bot fonctionne automatiquement et envoie les prédictions dans le canal configuré.

**Support:** @Kouamappoloak""")

@client.on(events.NewMessage(pattern='/stop'))
async def cmd_stop(event):
    if event.sender_id != ADMIN_ID:
        return
    global predictions_enabled
    predictions_enabled = False
    await event.respond("🛑 **PRÉDICTIONS ARRÊTÉES**")

@client.on(events.NewMessage(pattern='/forcestop'))
async def cmd_forcestop(event):
    """Force l'arrêt complet et débloque le système"""
    if event.sender_id != ADMIN_ID:
        return

    global predictions_enabled, verification_state, already_predicted_games

    predictions_enabled = False
    old_pred = verification_state['predicted_number']

    verification_state = {
        'predicted_number': None, 'predicted_suit': None,
        'current_check': 0, 'message_id': None,
        'channel_id': None, 'status': None, 'base_game': None
    }

    already_predicted_games.clear()

    msg = "🚨 **ARRÊT FORCÉ**\n\n"
    msg += f"🛑 Prédictions désactivées\n"
    msg += f"🔓 Système débloqué"
    if old_pred:
        msg += f"\n🗑️ Prédiction #{old_pred} effacée"

    await event.respond(msg)

@client.on(events.NewMessage(pattern='/resume'))
async def cmd_resume(event):
    if event.sender_id != ADMIN_ID:
        return
    global predictions_enabled
    predictions_enabled = True
    await event.respond("🚀 **PRÉDICTIONS REPRISES**")

@client.on(events.NewMessage(pattern='/predictinfo'))
async def cmd_predictinfo(event):
    if event.sender_id != ADMIN_ID:
        return

    verif_info = "Aucune"
    if verification_state['predicted_number']:
        next_check = verification_state['predicted_number'] + verification_state['current_check']
        verif_info = f"""#{verification_state['predicted_number']} ({verification_state['predicted_suit']})
Check: {verification_state['current_check']}/3
Attend: #{next_check}"""

    cycle_mins = [x//60 for x in pause_config['cycle']]
    current_idx = pause_config['current_index'] % len(pause_config['cycle'])
    next_pause_idx = (pause_config['current_index']) % len(pause_config['cycle'])

    # Calculer temps depuis dernière prédiction
    time_since_last = "N/A"
    if last_prediction_time:
        seconds = (datetime.now() - last_prediction_time).total_seconds()
        mins = int(seconds // 60)
        time_since_last = f"{mins} min"

    await event.respond(f"""📊 **STATUT SYSTÈME**

🎯 Source: #{current_game_number}
🔍 Vérification: {verif_info}
🟢 Prédictions: {'ON' if predictions_enabled else 'OFF'}
⏱️ Dernière activité: {time_since_last}

⏸️ **CYCLE DE PAUSE:**
• Actif: {'Oui' if pause_config['is_paused'] else 'Non'}
• Compteur: {pause_config['predictions_count']}/5
• Cycle: {cycle_mins} minutes
• Position: {current_idx + 1}/{len(cycle_mins)}
• Prochaine pause: {cycle_mins[next_pause_idx]} min

💡 /pausecycle pour modifier
💡 /clearverif si bloqué
💡 /forcestop pour débloquer""")

@client.on(events.NewMessage(pattern='/clearverif'))
async def cmd_clearverif(event):
    if event.sender_id != ADMIN_ID:
        return

    global verification_state
    old = verification_state['predicted_number']

    verification_state = {
        'predicted_number': None, 'predicted_suit': None,
        'current_check': 0, 'message_id': None,
        'channel_id': None, 'status': None, 'base_game': None
    }

    await event.respond(f"✅ **{'Vérification #' + str(old) + ' effacée' if old else 'Aucune vérification'}**\n🚀 Système libéré")

@client.on(events.NewMessage(pattern=r'^/pausecycle(\s*[\d\s,]*)?$'))
async def cmd_pausecycle(event):
    """Configure le cycle de pause"""
    if event.sender_id != ADMIN_ID:
        return

    message_text = event.message.message.strip()
    parts = message_text.split()

    if len(parts) == 1:
        cycle_mins = [x//60 for x in pause_config['cycle']]
        current_idx = pause_config['current_index'] % len(cycle_mins)

        next_pauses = []
        for i in range(3):
            idx = (pause_config['current_index'] + i) % len(cycle_mins)
            next_pauses.append(f"{cycle_mins[idx]}min")

        await event.respond(f"""⏸️ **CONFIGURATION CYCLE DE PAUSE**

**Cycle configuré:** {cycle_mins} minutes
**Ordre d'exécution:** {' → '.join([f'{m}min' for m in cycle_mins])} → recommence

**État actuel:**
• Position: {current_idx + 1}/{len(cycle_mins)}
• Compteur: {pause_config['predictions_count']}/5 prédictions
• Prochaines pauses: {' → '.join(next_pauses)}

**Modifier le cycle:**
`/pausecycle 3,5,4` (minutes, séparées par virgule)
`/pausecycle 5,10,7,3` (autant de valeurs que voulu)

**Fonctionnement:**
Après chaque 5 prédictions → pause selon le cycle configuré""")
        return

    try:
        cycle_str = ' '.join(parts[1:])
        cycle_str = cycle_str.replace(' ', '').replace(',', ',')
        new_cycle_mins = [int(x.strip()) for x in cycle_str.split(',') if x.strip()]

        if not new_cycle_mins or any(x <= 0 for x in new_cycle_mins):
            await event.respond("❌ Le cycle doit contenir des nombres positifs (minutes)")
            return

        new_cycle = [x * 60 for x in new_cycle_mins]
        pause_config['cycle'] = new_cycle
        pause_config['current_index'] = 0
        save_json(PAUSE_CONFIG_FILE, pause_config)

        await event.respond(f"""✅ **CYCLE MIS À JOUR**

**Nouveau cycle:** {new_cycle_mins} minutes
**Ordre:** {' → '.join([f'{m}min' for m in new_cycle_mins])} → recommence

🔄 Prochaine série: 5 prédictions puis {new_cycle_mins[0]} minutes de pause""")

    except Exception as e:
        await event.respond(f"❌ Erreur: {e}\n\nFormat: `/pausecycle 3,5,4`")

@client.on(events.NewMessage(pattern=r'^/setchannel(\s+.+)?$'))
async def cmd_setchannel(event):
    if event.sender_id != ADMIN_ID:
        return

    parts = event.message.message.strip().split()

    if len(parts) < 3:
        await event.respond(f"""📺 **CONFIGURATION CANAUX**

**Actuel:**
• Source: `{get_source_channel_id()}`
• Prédiction: `{get_prediction_channel_id()}`

**Modifier:**
`/setchannel source -1001234567890`
`/setchannel prediction -1001234567890`""")
        return

    try:
        ctype = parts[1].lower()
        cid = int(parts[2])

        if ctype == 'source':
            set_channels(source_id=cid)
            await event.respond(f"✅ **Canal source:**\n`{cid}`")

        elif ctype == 'prediction':
            set_channels(prediction_id=cid)
            await event.respond(f"✅ **Canal prédiction:**\n`{cid}`\n\n🎯 Les prédictions seront envoyées ici")
        else:
            await event.respond("❌ Type invalide. Utilisez: source ou prediction")

    except Exception as e:
        await event.respond(f"❌ Erreur: {e}")

@client.on(events.NewMessage(pattern='/bilan'))
async def cmd_bilan(event):
    if event.sender_id != ADMIN_ID:
        return

    if stats_bilan['total'] == 0:
        await event.respond("📊 Aucune prédiction enregistrée")
        return

    win_rate = (stats_bilan['wins'] / stats_bilan['total']) * 100

    await event.respond(f"""📊 **BILAN PRÉDICTIONS**

🎯 **Total:** {stats_bilan['total']}
✅ **Victoires:** {stats_bilan['wins']} ({win_rate:.1f}%)
❌ **Défaites:** {stats_bilan['losses']}

**Détails victoires:**
• Immédiat (N): {stats_bilan['win_details'].get('✅0️⃣', 0)}
• 2ème chance (N+1): {stats_bilan['win_details'].get('✅1️⃣', 0)}
• 3ème chance (N+2): {stats_bilan['win_details'].get('✅2️⃣', 0)}
• 4ème chance (N+3): {stats_bilan['win_details'].get('✅3️⃣', 0)}""")

@client.on(events.NewMessage(pattern='/reset'))
async def cmd_reset(event):
    """Reset uniquement les stats"""
    if event.sender_id != ADMIN_ID:
        return

    global stats_bilan, already_predicted_games, verification_state, last_prediction_time

    old_pred = verification_state['predicted_number']

    stats_bilan = {
        'total': 0, 'wins': 0, 'losses': 0,
        'win_details': {'✅0️⃣': 0, '✅1️⃣': 0, '✅2️⃣': 0, '✅3️⃣': 0},
        'loss_details': {'❌': 0}
    }

    already_predicted_games.clear()

    verification_state = {
        'predicted_number': None, 'predicted_suit': None,
        'current_check': 0, 'message_id': None,
        'channel_id': None, 'status': None, 'base_game': None
    }

    last_prediction_time = datetime.now()

    await event.respond(f"""🚨 **RESET EFFECTUÉ**

✅ **Réinitialisé:**
• Statistiques prédictions
• Historique prédictions{f" (#{old_pred})" if old_pred else ""}
• Système de vérification débloqué
• Timer de surveillance réinitialisé

🚀 Système prêt!""")

# ============================================================
# GESTION MESSAGES SOURCE
# ============================================================

@client.on(events.NewMessage)
async def handle_messages(event):
    # Canal source
    if event.is_group or event.is_channel:
        if event.chat_id == get_source_channel_id():
            await process_source_message(event)
        return

    # Commandes ignorées
    if event.message.message.startswith('/'):
        return

@client.on(events.MessageEdited)
async def handle_edit(event):
    if event.is_group or event.is_channel:
        if event.chat_id == get_source_channel_id():
            await process_source_message(event, is_edit=True)

# ============================================================
# SERVEUR WEB
# ============================================================

async def web_index(request):
    cycle_mins = [x//60 for x in pause_config['cycle']]
    current_idx = pause_config['current_index'] % len(cycle_mins)

    time_since_last = "N/A"
    if last_prediction_time:
        seconds = (datetime.now() - last_prediction_time).total_seconds()
        mins = int(seconds // 60)
        time_since_last = f"{mins} min"

    html = f"""<!DOCTYPE html>
<html>
<head><title>Bot Baccarat</title>
<style>
body {{ font-family: Arial; background: linear-gradient(135deg, #1e3c72, #2a5298); color: white; text-align: center; padding: 50px; }}
.status {{ background: rgba(255,255,255,0.1); padding: 20px; border-radius: 10px; display: inline-block; margin: 10px; min-width: 120px; }}
.number {{ font-size: 2em; color: #ffd700; font-weight: bold; }}
.label {{ font-size: 0.9em; opacity: 0.8; margin-bottom: 5px; }}
</style></head>
<body>
<h1>🎰 Bot Baccarat</h1>
<div class="status"><div class="label">Jeu Actuel</div><div class="number">#{current_game_number}</div></div>
<div class="status"><div class="label">Vérification</div><div class="number">{verification_state['predicted_number'] or 'Libre'}</div></div>
<div class="status"><div class="label">Prédictions</div><div class="number">{'🟢 ON' if predictions_enabled else '🔴 OFF'}</div></div>
<div class="status"><div class="label">Dernière Activité</div><div class="number">{time_since_last}</div></div>
<div class="status"><div class="label">Pause</div><div class="number">{pause_config['predictions_count']}/5</div></div>
<p style="margin-top: 30px; opacity: 0.8;">
⏸️ Cycle: {cycle_mins} min | Position: {current_idx + 1}/{len(cycle_mins)} | {'⏸️ EN PAUSE' if pause_config['is_paused'] else '▶️ ACTIF'}
</p>
<p>🔄 {datetime.now().strftime('%H:%M:%S')}</p>
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
    global auto_reset_task, last_prediction_time

    load_all_configs()
    await start_web()
    await client.start(bot_token=BOT_TOKEN)

    # Initialiser le timer au démarrage
    last_prediction_time = datetime.now()

    # Démarrer le moniteur de reset automatique
    auto_reset_task = asyncio.create_task(auto_reset_monitor())

    cycle_mins = [x//60 for x in pause_config['cycle']]

    logger.info("=" * 60)
    logger.info("🚀 BOT BACCARAT DÉMARRÉ")
    logger.info(f"👑 Admin ID: {ADMIN_ID}")
    logger.info(f"📺 Source: {get_source_channel_id()}")
    logger.info(f"🎯 Prédiction: {get_prediction_channel_id()}")
    logger.info(f"⏸️ Cycle pause: {cycle_mins} min")
    logger.info(f"⏸️ Position cycle: {(pause_config['current_index'] % len(cycle_mins)) + 1}/{len(cycle_mins)}")
    logger.info("🔄 Reset automatique: ACTIVÉ (20 min)")
    logger.info("=" * 60)

    await client.run_until_disconnected()

if __name__ == '__main__':
    asyncio.run(main())
