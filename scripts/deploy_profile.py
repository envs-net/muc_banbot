"""muc_banbot deployment profile for envs-xmpp."""
from envs_xmpp_ops.profile import DeploymentProfile

PROFILE = DeploymentProfile(
    app_name="muc_banbot",
    executable="muc_banbot",
    service_name="muc_banbot.service",
    config_environment="MUC_BANBOT_CONFIG",
    default_config="/etc/muc_banbot/config.py",
    default_data="/var/lib/muc_banbot",
    service_user="adminbot",
    service_group="adminbot",
    venv_name="venv",
)
