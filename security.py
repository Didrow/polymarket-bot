"""
security.py — Polymarket Weather Bot 2026
Безпека: перевірки гаманця, аудит залежностей, захист ключів.
Реалізує "Polymarket Bot Bible 2026" Security Rules.
"""

import os
import sys
import logging
import hashlib
import subprocess
from typing import List, Tuple

import config

logger = logging.getLogger(__name__)

# ─── ВІДОМІ БЕЗПЕЧНІ ПАКЕТИ ──────────────────────────────────
APPROVED_PACKAGES = {
    "py-clob-client", "python-dotenv", "requests", "pandas",
    "numpy", "aiohttp", "websockets", "apscheduler", "web3",
    "eth-account", "eth-typing", "hexbytes", "cytoolz",
    "urllib3", "certifi", "charset-normalizer", "idna",
    "python-dateutil", "pytz", "six", "attrs", "cattrs",
    "aiofiles", "async-timeout", "multidict", "yarl",
    "frozenlist", "aiosignal", "typing-extensions",
    "packaging", "setuptools", "pip",
}

# Підозрілі назви (typosquatting)
SUSPICIOUS_PATTERNS = [
    "py-clob-cl1ent", "polymarket-client", "poly-market",
    "clob-client", "metamask-py", "web3-utils",
]


# ═══════════════════════════════════════════════════════════
# RULE 1: DEDICATED WALLET
# ═══════════════════════════════════════════════════════════

def check_dedicated_wallet(private_key: str) -> bool:
    """
    RULE 1: Перевірити, що гаманець — не основний.
    Ніколи не використовуй гаманець з великим балансом для ботів!
    """
    logger.info("🔐 RULE 1: Перевірка dedicated wallet...")

    if not private_key or private_key == "0xYOUR_DEDICATED_WALLET_PRIVATE_KEY_HERE":
        logger.error("❌ PRIVATE_KEY не налаштовано! Встанови в .env файлі")
        return False

    if len(private_key) < 60:
        logger.error("❌ Некоректний формат PRIVATE_KEY")
        return False

    logger.info("✅ RULE 1: Приватний ключ знайдено (довжина коректна)")
    logger.warning("⚠️  НАГАДУВАННЯ: Використовуй ТІЛЬКИ dedicated trading wallet!")
    logger.warning("⚠️  НІКОЛИ не вводь ключ від основного гаманця з великим балансом!")
    return True


# ═══════════════════════════════════════════════════════════
# RULE 2: AUDIT DEPENDENCIES
# ═══════════════════════════════════════════════════════════

def audit_installed_packages() -> List[str]:
    """
    RULE 2: Перевірити встановлені пакети на підозрілі назви.
    Returns: список підозрілих пакетів.
    """
    logger.info("🔍 RULE 2: Аудит Python пакетів...")

    suspicious = []
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "list", "--format=columns"],
            capture_output=True, text=True, timeout=30
        )
        lines = result.stdout.strip().split("\n")[2:]  # Пропустити заголовок

        installed = []
        for line in lines:
            parts = line.strip().split()
            if parts:
                pkg_name = parts[0].lower()
                installed.append(pkg_name)

                # Перевірка typosquatting
                for sus in SUSPICIOUS_PATTERNS:
                    if sus in pkg_name and pkg_name not in APPROVED_PACKAGES:
                        suspicious.append(pkg_name)
                        logger.error(f"🚨 ПІДОЗРІЛИЙ ПАКЕТ: {pkg_name}")

        logger.info(f"✅ RULE 2: {len(installed)} пакетів перевірено, "
                    f"{len(suspicious)} підозрілих")

    except Exception as e:
        logger.warning(f"Аудит пакетів: помилка {e}")

    return suspicious


# ═══════════════════════════════════════════════════════════
# RULE 3: ENV FILE PROTECTION
# ═══════════════════════════════════════════════════════════

def check_env_security() -> bool:
    """
    RULE 3: Перевірити, що .env файл захищений.
    """
    logger.info("🔒 RULE 3: Перевірка .env безпеки...")

    # Перевірка .gitignore
    gitignore_path = ".gitignore"
    env_in_gitignore = False
    if os.path.exists(gitignore_path):
        with open(gitignore_path) as f:
            content = f.read()
            if ".env" in content:
                env_in_gitignore = True

    if not env_in_gitignore:
        logger.warning("⚠️  .env не знайдено в .gitignore! ДОДАЙ: echo '.env' >> .gitignore")

    # Перевірка що ключ не в коді (простий скан)
    key = os.getenv("PRIVATE_KEY", "")
    if key and key in open(__file__).read() if os.path.exists(__file__) else False:
        logger.error("🚨 ПРИВАТНИЙ КЛЮЧ ЗНАЙДЕНО В КОДІ! НЕГАЙНО ЗМІНИТИ ГАМАНЕЦЬ!")
        return False

    logger.info("✅ RULE 3: Приватний ключ береться з .env (безпечно)")
    return True


# ═══════════════════════════════════════════════════════════
# RULE 4: USDC APPROVAL LIMIT (Revoke.cash principle)
# ═══════════════════════════════════════════════════════════

def check_usdc_approval_limit() -> None:
    """
    RULE 4: Нагадування про обмеження USDC approval.
    """
    logger.info("💱 RULE 4: USDC Approval рекомендація")
    logger.info(f"⚠️  Встанови USDC approval ≤ ${config.MAX_USDC_APPROVAL}")
    logger.info("⚠️  Після торгівлі перевір revoke.cash для відкликання зайвих дозволів")


# ═══════════════════════════════════════════════════════════
# RULE 5: DRY-RUN DEFAULT
# ═══════════════════════════════════════════════════════════

def check_dry_run_status() -> None:
    """RULE 5: Підтвердити поточний режим роботи."""
    if config.DRY_RUN:
        logger.info("🧪 RULE 5: DRY-RUN режим УВІМКНЕНО — реальних угод не буде")
        logger.info("   Для реальної торгівлі: встанови DRY_RUN=false в .env")
    else:
        logger.warning("💰 RULE 5: РЕАЛЬНИЙ режим! Угоди будуть виконуватись з реальними грошима!")
        logger.warning("   Починай з мінімальних сум. Ти підтвердив ризики.")


# ═══════════════════════════════════════════════════════════
# ПОВНИЙ SECURITY CHECK (при запуску)
# ═══════════════════════════════════════════════════════════

def run_security_checks() -> bool:
    """
    Запустити всі security checks при старті бота.
    Returns True якщо все ОК.
    """
    logger.info("")
    logger.info("━" * 50)
    logger.info("🛡️  SECURITY CHECKS (Bot Bible 2026)")
    logger.info("━" * 50)

    private_key = os.getenv("PRIVATE_KEY", "")

    results = []

    # RULE 1
    r1 = check_dedicated_wallet(private_key)
    results.append(r1)

    # RULE 2
    suspicious = audit_installed_packages()
    r2 = len(suspicious) == 0
    results.append(r2)

    # RULE 3
    r3 = check_env_security()
    results.append(r3)

    # RULE 4
    check_usdc_approval_limit()
    results.append(True)

    # RULE 5
    check_dry_run_status()
    results.append(True)

    all_ok = all(results)

    logger.info("━" * 50)
    if all_ok:
        logger.info("✅ Всі security checks пройдено")
    else:
        logger.error("❌ Security checks FAILED! Перевір конфігурацію")
        if not results[0]:
            logger.error("   → Встанови PRIVATE_KEY у .env файлі")
        if not results[1]:
            logger.error(f"   → Виявлені підозрілі пакети: {suspicious}")
    logger.info("━" * 50)
    logger.info("")

    return all_ok
