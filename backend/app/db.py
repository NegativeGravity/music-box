import json
import time
import uuid
from sqlalchemy import Boolean, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = 'users'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    email: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    name: Mapped[str] = mapped_column(String, default='Listener')
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    preferences: Mapped[dict] = mapped_column(JSON, default=lambda: {'genre': 'Electronic', 'mode': 'balanced', 'seed_track_ids': [], 'personalization_enabled': False})
    profile_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class Session(Base):
    __tablename__ = 'sessions'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    csrf: Mapped[str] = mapped_column(String)
    expires: Mapped[float] = mapped_column(Float)


class Track(Base):
    __tablename__ = 'tracks'
    __table_args__ = (UniqueConstraint('owner_id', 'upload_key'),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[str | None] = mapped_column(ForeignKey('users.id'), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String)
    artist: Mapped[str] = mapped_column(String, default='Unknown artist')
    genre: Mapped[str] = mapped_column(String)
    source: Mapped[str] = mapped_column(String, default='upload')
    visibility: Mapped[str] = mapped_column(String, default='private', index=True)
    provenance: Mapped[str] = mapped_column(Text, default='User supplied private audio')
    can_play: Mapped[bool] = mapped_column(Boolean, default=True)
    can_analyze: Mapped[bool] = mapped_column(Boolean, default=True)
    can_recommend: Mapped[bool] = mapped_column(Boolean, default=True)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    original_key: Mapped[str] = mapped_column(String)
    stream_key: Mapped[str | None] = mapped_column(String, nullable=True)
    sha256: Mapped[str] = mapped_column(String)
    duration: Mapped[float] = mapped_column(Float, default=0)
    size: Mapped[int] = mapped_column(Integer, default=0)
    playback_status: Mapped[str] = mapped_column(String, default='queued')
    embedding_status: Mapped[str] = mapped_column(String, default='pending')
    embedding_model: Mapped[str | None] = mapped_column(String, nullable=True)
    embedding: Mapped[list | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    color: Mapped[str] = mapped_column(String, default='#8de0c5')
    upload_key: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class Job(Base):
    __tablename__ = 'audio_jobs'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    track_id: Mapped[int] = mapped_column(ForeignKey('tracks.id'), index=True)
    stage: Mapped[str] = mapped_column(String, default='decode')
    status: Mapped[str] = mapped_column(String, default='queued', index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    leased_until: Mapped[float] = mapped_column(Float, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class Feedback(Base):
    __tablename__ = 'feedback'
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), primary_key=True)
    track_id: Mapped[int] = mapped_column(ForeignKey('tracks.id'), primary_key=True)
    signal: Mapped[str] = mapped_column(String)


class GenerationJob(Base):
    __tablename__ = 'generation_jobs'
    __table_args__ = (UniqueConstraint('owner_id', 'idempotency_key'),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    owner_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    idempotency_key: Mapped[str] = mapped_column(String)
    request_hash: Mapped[str] = mapped_column(String)
    request_snapshot: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String, default='queued', index=True)
    phase: Mapped[str] = mapped_column(String, default='queued')
    upstream_task_id: Mapped[str | None] = mapped_column(String, nullable=True)
    output_key: Mapped[str | None] = mapped_column(String, nullable=True)
    saved_track_id: Mapped[int | None] = mapped_column(ForeignKey('tracks.id'), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time)
    leased_until: Mapped[float] = mapped_column(Float, default=0)
    lease_owner: Mapped[str | None] = mapped_column(String, nullable=True)
    started_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    elapsed_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    candidates: Mapped[list['GenerationCandidate']] = relationship(cascade='all, delete-orphan', lazy='selectin', order_by='GenerationCandidate.index')


class GenerationCandidate(Base):
    __tablename__ = 'generation_candidates'
    __table_args__ = (UniqueConstraint('generation_id', 'index'),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    generation_id: Mapped[str] = mapped_column(ForeignKey('generation_jobs.id', ondelete='CASCADE'), index=True)
    index: Mapped[int] = mapped_column(Integer)
    seed: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String, default='queued')
    attempt_id: Mapped[str | None] = mapped_column(String, nullable=True)
    process_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    process_started_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    process_command: Mapped[list] = mapped_column(JSON, default=list)
    output_key: Mapped[str | None] = mapped_column(String, nullable=True)
    saved_track_id: Mapped[int | None] = mapped_column(ForeignKey('tracks.id'), nullable=True)
    diagnostics: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class Event(Base):
    __tablename__ = 'events'
    __table_args__ = (UniqueConstraint('user_id', 'event_id'),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    event_id: Mapped[str] = mapped_column(String)
    event_type: Mapped[str] = mapped_column(String)
    track_id: Mapped[int] = mapped_column(ForeignKey('tracks.id'))
    request_id: Mapped[str | None] = mapped_column(String, nullable=True)
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class Recommendation(Base):
    __tablename__ = 'recommendation_requests'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    response: Mapped[dict] = mapped_column(JSON)
    profile_snapshot: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class Playlist(Base):
    __tablename__ = 'playlists'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    title: Mapped[str] = mapped_column(String)
    track_ids: Mapped[list] = mapped_column(JSON, default=list)


def database(url):
    engine = create_engine(url, connect_args={'check_same_thread': False, 'timeout': 30} if url.startswith('sqlite') else {}, pool_pre_ping=True)
    if url.startswith('sqlite'):
        from sqlalchemy import event
        @event.listens_for(engine, 'connect')
        def configure(connection, _):
            connection.execute('PRAGMA journal_mode=WAL')
            connection.execute('PRAGMA foreign_keys=ON')
    Base.metadata.create_all(engine)
    return engine, sessionmaker(engine, expire_on_commit=False)
