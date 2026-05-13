from banbot.ignorelist import IgnorelistMixin
from banbot.utils import bare_jid


class IgnoreBot(IgnorelistMixin):
    def __init__(self):
        self.ignore_jids = {"admin@example.org"}
        self.ignore_domains = {"example.org", "trusted.net"}

    def bare_jid(self, jid):
        return bare_jid(jid)


def test_ignore_jid_exact_match_only():
    bot = IgnoreBot()
    assert bot.is_ignored_jid("admin@example.org/resource")
    assert not bot.is_ignored_jid("user@example.org")


def test_ignore_domain_matches_subdomains():
    bot = IgnoreBot()
    assert bot.is_ignored_domain("example.org")
    assert bot.is_ignored_domain("sub.example.org")
    assert bot.is_ignored_domain("*.trusted.net")
    assert not bot.is_ignored_domain("evil.org")


def test_ignore_target_semantics():
    bot = IgnoreBot()
    assert bot.is_ignored_target("admin@example.org")
    assert bot.is_ignored_target("*.example.org")
    assert bot.is_ignored_target("example.org")
    assert not bot.is_ignored_target("user@example.org")
    assert bot.is_ignored_target("user@example.org", include_domain_for_jid=True)
