#!/bin/sh
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
bin_dir="$HOME/.local/bin"
data_dir="${XDG_DATA_HOME:-$HOME/.local/share}"

mkdir -p "$bin_dir"
ln -sfn "$project_dir/bin/clicky" "$bin_dir/heycliky"
install -Dm644 "$project_dir/assets/heycliky.svg" "$data_dir/icons/hicolor/scalable/apps/heycliky.svg"
install -Dm644 "$project_dir/heycliky.desktop" "$data_dir/applications/heycliky.desktop"

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$data_dir/applications"
fi

printf '%s\n' "Installed Hey Clicky. Run: heycliky run"
