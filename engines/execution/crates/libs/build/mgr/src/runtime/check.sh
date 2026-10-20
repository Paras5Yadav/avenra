# Modified for Avenra. Original attribution and adaptation record: LICENSES/.
set -eu
# These checks concern native output only; legacy-only templates keep working.
case " $(cat /proc/cmdline) " in
    *" systemd.mask=avenra-apps.target "*) ;;
    *) echo 'builder app mask missing; upgrade the sandbox manager' >&2; exit 1;;
esac
systemctl is-active --quiet space.target
# Old guests need no new agent, but must already enforce the user-work envelope.
case "$(systemctl show space.slice --property=MemoryMax --value)" in
    ''|infinity|0|*[!0-9]*) echo 'guest user-work memory limit missing; rebuild base' >&2; exit 1;;
esac
/usr/bin/env --chdir=/ -- /bin/true
for directory in /etc/systemd/system /run/systemd/system /usr/lib/systemd/system /lib/systemd/system; do
    for name in e2b-template-start.service avenra-start-cmd.service; do
        for path in "$directory/$name" "$directory"/*.wants/"$name" "$directory"/*.requires/"$name"; do
            if [ -e "$path" ] || [ -L "$path" ]; then
                echo 'rebuild legacy startup from recipe before setting or clearing native apps' >&2
                exit 1
            fi
        done
    done
    for path in "$directory"/avenra-runtime@*.service.d "$directory"/avenra-apps.target.d; do
        if [ -e "$path" ] || [ -L "$path" ]; then
            echo 'runtime unit overrides are unsupported in template builds' >&2
            exit 1
        fi
    done
    # Apps are activated only by the group target, never independently enabled.
    for path in "$directory"/*.wants/avenra-runtime@*.service "$directory"/*.requires/avenra-runtime@*.service; do
        if [ -e "$path" ] || [ -L "$path" ]; then
            echo 'native apps must be activated through avenra-apps.target; remove the independent enablement link' >&2
            exit 1
        fi
    done
done
