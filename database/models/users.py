from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Identity as SAIdentity,
    Integer,
    String,
    Text,
)

from ._base import Base, DictLikeMixin


class User(DictLikeMixin, Base):
    __tablename__ = "users"

    id = Column(BigInteger, SAIdentity(always=False), primary_key=True)
    tg_id = Column(BigInteger, nullable=True, unique=True)
    identity_id = Column(
        String(36),
        ForeignKey("identities.id", ondelete="SET NULL", onupdate="CASCADE"),
        nullable=True,
        index=True,
    )
    username = Column(String)
    first_name = Column(String)
    last_name = Column(String)
    language_code = Column(String)
    is_bot = Column(Boolean, default=False)
    balance = Column(Float, default=0.0)
    trial = Column(Integer, default=0)
    preferred_currency = Column(String(10), nullable=False, server_default="RUB", index=True)
    source_code = Column(
        String,
        ForeignKey(
            "tracking_sources.code",
            ondelete="SET NULL",
            onupdate="CASCADE",
        ),
        nullable=True,
    )
    legal_accepted_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow)


class ManualBan(DictLikeMixin, Base):
    __tablename__ = "manual_bans"

    user_id = Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, primary_key=True)
    tg_id = Column(BigInteger, nullable=True, index=True)
    banned_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    reason = Column(Text)
    # reason видят только админы; клиенту показывается public_reason или нейтральный текст
    public_reason = Column(Text, nullable=True)
    banned_by = Column(BigInteger)
    until = Column(DateTime(timezone=True), nullable=True)


class PreBan(DictLikeMixin, Base):
    """Бан по tg id заранее, до регистрации: без строки в users, поэтому отдельно от manual_bans."""

    __tablename__ = "pre_bans"

    tg_id = Column(BigInteger, primary_key=True)
    reason = Column(Text)
    # reason видят только админы; клиенту показывается public_reason или нейтральный текст
    public_reason = Column(Text, nullable=True)
    source = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)


class TemporaryData(DictLikeMixin, Base):
    __tablename__ = "temporary_data"

    user_id = Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, primary_key=True)
    tg_id = Column(BigInteger, nullable=True, index=True)
    state = Column(String)
    data = Column(JSON)
    updated_at = Column(DateTime, default=datetime.utcnow)


class BlockedUser(DictLikeMixin, Base):
    __tablename__ = "blocked_users"

    user_id = Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, primary_key=True)
    tg_id = Column(BigInteger, nullable=True, index=True)


class TrackingSource(DictLikeMixin, Base):
    __tablename__ = "tracking_sources"

    id = Column(Integer, primary_key=True)
    name = Column(String)
    code = Column(String, unique=True)
    type = Column(String)
    created_by = Column(BigInteger)
    created_at = Column(DateTime, default=datetime.utcnow)
