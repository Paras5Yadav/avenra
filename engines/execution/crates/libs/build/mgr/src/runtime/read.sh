# Modified for Avenra. Original attribution and adaptation record: LICENSES/.
set -eu
if [ -e /etc/avenra/apps ] || [ -L /etc/avenra/apps ]; then
    test ! -L /etc/avenra/apps
    test -d /etc/avenra/apps
    for unit in /etc/avenra/apps/*.service /etc/avenra/apps/*.target; do
        test "$(readlink "/etc/systemd/system/${unit##*/}")" = "$unit"
    done
    test "$(readlink /etc/systemd/system/space.target.wants/avenra-apps.target)" = /etc/avenra/apps/avenra-apps.target
    for unit in /etc/systemd/system/avenra-runtime@*.service; do
        test -f "/etc/avenra/apps/${unit##*/}"
    done
    tar -cf - -C /etc/avenra/apps .
else
    for path in /etc/systemd/system/avenra-runtime@*.service /etc/systemd/system/avenra-apps.target /etc/systemd/system/space.target.wants/avenra-apps.target; do
        if [ -e "$path" ] || [ -L "$path" ]; then
            echo 'orphaned native startup definitions; use the native recipe operations' >&2
            exit 1
        fi
    done
fi
