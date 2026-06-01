"""Shared lock helper tests."""

from __future__ import annotations

import pytest

from banbot.locks import (
    ban_state_lock,
    database_file_lock,
    database_mutation_locks,
    get_ban_state_lock,
    get_database_file_lock,
    is_maintenance_mode,
    maintenance_operation,
)


class LockBot:
    pass


@pytest.mark.asyncio
async def test_database_mutation_locks_enable_maintenance_mode_for_lightweight_objects():
    bot = LockBot()

    assert is_maintenance_mode(bot) is False
    async with database_mutation_locks(bot):
        assert is_maintenance_mode(bot) is True
        async with database_mutation_locks(bot):
            assert is_maintenance_mode(bot) is True
    assert is_maintenance_mode(bot) is False


@pytest.mark.asyncio
async def test_lock_helpers_use_explicit_locks_when_available():
    import asyncio

    bot = LockBot()
    db_lock = asyncio.Lock()
    ban_lock = asyncio.Lock()
    bot._database_file_operation_lock = db_lock
    bot._ban_state_operation_lock = ban_lock

    assert get_database_file_lock(bot) is db_lock
    assert get_ban_state_lock(bot) is ban_lock


@pytest.mark.asyncio
async def test_lock_helpers_reuse_fallback_locks_and_are_reentrant():
    bot = LockBot()

    assert get_database_file_lock(bot) is get_database_file_lock(bot)
    assert get_ban_state_lock(bot) is get_ban_state_lock(bot)

    async with database_file_lock(bot):
        assert getattr(bot, "_database_file_lock_depth") == 1
        async with database_file_lock(bot):
            assert getattr(bot, "_database_file_lock_depth") == 2
        assert getattr(bot, "_database_file_lock_depth") == 1
    assert getattr(bot, "_database_file_lock_depth") == 0

    async with ban_state_lock(bot):
        assert getattr(bot, "_ban_state_lock_depth") == 1
        async with ban_state_lock(bot):
            assert getattr(bot, "_ban_state_lock_depth") == 2
        assert getattr(bot, "_ban_state_lock_depth") == 1
    assert getattr(bot, "_ban_state_lock_depth") == 0


async def _raise_inside_maintenance_operation(bot):
    async with maintenance_operation(bot):
        assert is_maintenance_mode(bot) is True
        raise RuntimeError("boom")


async def _raise_inside_database_file_lock(bot):
    async with database_file_lock(bot):
        assert getattr(bot, "_database_file_lock_depth") == 1
        raise RuntimeError("db boom")


async def _raise_inside_ban_state_lock(bot):
    async with ban_state_lock(bot):
        assert getattr(bot, "_ban_state_lock_depth") == 1
        raise RuntimeError("ban boom")


@pytest.mark.asyncio
async def test_maintenance_operation_clears_depth_after_exception():
    bot = LockBot()

    with pytest.raises(RuntimeError, match="boom"):
        await _raise_inside_maintenance_operation(bot)

    assert is_maintenance_mode(bot) is False


@pytest.mark.asyncio
async def test_individual_lock_helpers_clear_depth_after_exception():
    bot = LockBot()

    with pytest.raises(RuntimeError, match="db boom"):
        await _raise_inside_database_file_lock(bot)
    assert getattr(bot, "_database_file_lock_depth") == 0

    with pytest.raises(RuntimeError, match="ban boom"):
        await _raise_inside_ban_state_lock(bot)
    assert getattr(bot, "_ban_state_lock_depth") == 0

