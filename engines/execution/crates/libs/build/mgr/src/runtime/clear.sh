# Modified for Avenra. Original attribution and adaptation record: LICENSES/.
set -eu
rm -f /etc/systemd/system/space.target.wants/avenra-apps.target /etc/systemd/system/avenra-apps.target
# This namespace belongs to native runtime compilation, not arbitrary services.
rm -f /etc/systemd/system/avenra-runtime@*.service
rm -rf /etc/avenra/apps
