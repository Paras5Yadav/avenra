# Modified for Avenra. Original attribution and adaptation record: LICENSES/.
set -eu
test ! -L /etc/space
mkdir -p /etc/space
stage=$(mktemp -d /etc/avenra/.apps-build.XXXXXX)
trap 'rm -rf "$stage"' EXIT
# Input is the builder's own regular-file archive, never a tenant archive.
tar -xf - -C "$stage"
rm -f /etc/systemd/system/avenra-runtime@*.service
rm -rf /etc/avenra/apps
mv "$stage" /etc/avenra/apps
mkdir -p /etc/systemd/system/space.target.wants
for unit in /etc/avenra/apps/*.service /etc/avenra/apps/*.target; do
    ln -sfn "$unit" "/etc/systemd/system/${unit##*/}"
done
ln -sfn /etc/avenra/apps/avenra-apps.target /etc/systemd/system/space.target.wants/avenra-apps.target
