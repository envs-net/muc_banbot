"""Compatibility facade for shared Slixmpp MUC join helpers."""
from envs_xmpp_core.xmpp.muc_join import await_muc_join_compat, drain_task, start_muc_join_task

__all__ = ["await_muc_join_compat", "drain_task", "start_muc_join_task"]
