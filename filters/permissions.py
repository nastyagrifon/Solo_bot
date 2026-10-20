from __future__ import annotations


PERM_USERS = "users"
PERM_USERS_WRITE = "users_write"
PERM_BALANCE = "balance"
PERM_KEYS = "keys"
PERM_BULK = "bulk"
PERM_TARIFFS = "tariffs"
PERM_CLUSTERS = "clusters"
PERM_BROADCASTING = "broadcasting"
PERM_COUPONS = "coupons"
PERM_GIFTS = "gifts"
PERM_STATS = "stats"
PERM_ADS = "ads"
PERM_MODULES = "modules"
PERM_SETTINGS = "settings"
PERM_MANAGEMENT = "management"
PERM_ADMINS = "admins"
PERM_EMOJI = "emoji"


PERMISSION_LABELS: dict[str, str] = {
    PERM_USERS: "👤 Карточка клиента (смотреть)",
    PERM_USERS_WRITE: "✏️ Карточка клиента (правка)",
    PERM_BALANCE: "💵 Финансовые операции",
    PERM_BULK: "📦 Массовые действия",
    PERM_TARIFFS: "💸 Тарифы",
    PERM_CLUSTERS: "🖥️ Серверы",
    PERM_BROADCASTING: "📢 Рассылки",
    PERM_COUPONS: "🎟️ Купоны",
    PERM_GIFTS: "🎁 Подарки",
    PERM_STATS: "📊 Статистика",
    PERM_ADS: "📈 UTM / Аналитика",
    PERM_MODULES: "🧩 Модули",
    PERM_SETTINGS: "⚙️ Настройки",
    PERM_MANAGEMENT: "🤖 Управление ботом",
    PERM_ADMINS: "👑 Управление админами",
    PERM_EMOJI: "😀 Эмоджи",
}

ALL_PERMISSIONS: tuple[str, ...] = tuple(PERMISSION_LABELS.keys())


_LEGACY_PERMISSION_ALIASES: dict[str, str] = {
    PERM_KEYS: PERM_USERS,
}


# Набор прав по умолчанию на роль. Нужен потому, что админ создавался с пустым
# списком и выглядел сломанным: роль была, кнопки молчали.
ROLE_PRESETS: dict[str, tuple[str, ...]] = {
    "superadmin": ALL_PERMISSIONS,
    "admin": (
        PERM_USERS, PERM_USERS_WRITE, PERM_BALANCE, PERM_BULK, PERM_TARIFFS,
        PERM_CLUSTERS, PERM_BROADCASTING, PERM_COUPONS, PERM_GIFTS, PERM_STATS,
        PERM_ADS, PERM_EMOJI,
    ),
    # Суппорт ведёт карточку клиента целиком, включая начисления, списания и
    # компенсации.
    "support": (PERM_USERS, PERM_USERS_WRITE, PERM_BALANCE, PERM_GIFTS,
                PERM_STATS),
    # Тестировщик правит подписки и ключи, но денег не касается.
    "tester": (PERM_USERS, PERM_USERS_WRITE, PERM_STATS),
    # Маркетолог: купоны и акции, аналитика и рассылки. Карточку клиента
    # только смотрит, деньги не трогает.
    "marketer": (PERM_USERS, PERM_COUPONS, PERM_GIFTS, PERM_ADS, PERM_STATS,
                 PERM_BROADCASTING),
    # Сотрудник только смотрит.
    "staff": (PERM_USERS, PERM_STATS),
    "designer": (PERM_USERS, PERM_EMOJI),
}

# Роли, существующие в базе до этой правки. Прежний `moderator` по смыслу и
# есть суппорт, иначе у него пропадут кнопки после перехода.
ROLE_ALIASES: dict[str, str] = {"moderator": "support"}

# Единственная роль, которой права не проверяются.
SUPERADMIN_ROLE = "superadmin"

ALL_ROLES: tuple[str, ...] = tuple(ROLE_PRESETS.keys())


def normalize_role(raw) -> str:
    """Роль из базы в каноническую. Неизвестная роль — НЕ суперадмин.

    Прежде `is_super` считался как «любая роль, кроме moderator», поэтому
    опечатка в поле или новая роль давали полный доступ.
    """
    role = (raw or "").strip().lower()
    role = ROLE_ALIASES.get(role, role)
    return role if role in ROLE_PRESETS else "staff"


def permissions_for_role(role: str) -> tuple[str, ...]:
    """Права по умолчанию для роли."""
    return ROLE_PRESETS.get(normalize_role(role), ())


def normalize_permissions(raw) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, str):
        raw = [raw]
    seen: set[str] = set()
    result: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        item = _LEGACY_PERMISSION_ALIASES.get(item, item)
        if item in PERMISSION_LABELS and item not in seen:
            seen.add(item)
            result.append(item)
    return result
