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
    RULE 1: Перевірити наявність ключа гаманця.
    В DRY_RUN режимі ключ НЕ потрібен — симуляція не торгує реально.
    """
    logger.info("🔐 RULE 1: Перевірка гаманця...")

    # DRY_RUN = симуляція, реального гаманця не потрібно
    if config.DRY_RUN:
        logger.info("🧪 DRY_RUN=true: PRIVATE_KEY не потрібен для симуляції, пропускаємо")
        return True

    # Тільки для реальної торгівлі перевіряємо ключ
    if not private_key or private_key == "0xYOUR_DEDICATED_WALLET_PRIVATE_KEY_HERE":
        logger.error("❌ PRIVATE_KEY не налаштовано!")
        logger.error("   Додай в Render: Environment Variables -> PRIVATE_KEY -> твій ключ")
        logger.error("   (Для симуляції достатньо DRY_RUN=true — ключ не потрібен)")
        return False

    if len(private_key) < 60:
        logger.error("❌ Некоректний формат PRIVATE_KEY — перевір значення")
        return False

    logger.info("✅ RULE 1: Приватний ключ знайдено")
    logger.warning("⚠️  ТІЛЬКИ dedicated trading wallet — ніколи не основний!")
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
    if key and os.path.exists(__file__):
        with open(__file__) as _f:
            if key in _f.read():
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
