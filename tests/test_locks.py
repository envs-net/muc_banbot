"""Shared lock helper tests."""

from __future__ import annotations

import pytest

from banbot.locks import database_mutation_locks, is_maintenance_mode


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
