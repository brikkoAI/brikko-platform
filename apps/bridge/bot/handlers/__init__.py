"""Aiogram handler registration entry-point."""
from aiogram import Dispatcher

from bot.handlers.approval_callback import register_handlers as register_approval_cb
from bot.handlers.auth import register_handlers as register_auth
from bot.handlers.commands import register_handlers as register_commands
from bot.handlers.follow import register_handlers as register_follow
from bot.handlers.permissions import register_handlers as register_permissions
from bot.handlers.prompt import register_handlers as register_prompt


def register_all(dp: Dispatcher) -> None:
    register_auth(dp)
    register_commands(dp)
    register_permissions(dp)
    register_follow(dp)
    # callback_query routers — narrow filters so they don't collide.
    # cancel-callback is registered by prompt.register_handlers below;
    # approval-callback uses ``appr:`` prefix and is registered here.
    register_approval_cb(dp)
    # prompt handler is the catch-all — register LAST so command handlers win
    register_prompt(dp)
