#!/bin/bash
# Install the World in Brief push as a LaunchAgent on this Mac.
#   mac/install.sh primary   # QuickServers: fetches at the times below
#   mac/install.sh backup    # MacinCloud: 7 min later, only if the primary didn't push
# Run it over ssh: it also seeds the job's own Chrome profile from the everyday Chrome sign-in,
# which a LaunchAgent may not read itself under macOS privacy rules. (MacinCloud's data-center
# address gets Cloudflare's "Just a moment" check more often, so it is the backup.)
# Times are America/New_York. 04:45 lands before the newspaper's 5:00 am final pass; the
# 6:10 am print step asks for one more push on demand (family-newspaper tools/economist_news.py).
# The other times follow World in Brief's usual updates (~00:00, ~04:00-05:30 and through the day, ET).
set -euo pipefail
ROLE=${1:?primary or backup}
TIMES_ET="00:50 02:50 04:45 10:30 14:30 18:30"
DEST="$HOME/Library/Application Support/economist-wib"
PLIST="$HOME/Library/LaunchAgents/com.aifurman.economist-wib.plist"
mkdir -p "$DEST" "$HOME/Library/Logs"
cp "$(dirname "$0")/wib_push.py" "$DEST/wib_push.py"
[ -f "$HOME/.config/economist-wib/env" ] || { echo "missing ~/.config/economist-wib/env"; exit 1; }
[ -f "$DEST/chrome-profile/Default/Cookies" ] || /usr/bin/python3 "$DEST/wib_push.py" --seed
ARGS="<string>/usr/bin/python3</string><string>$DEST/wib_push.py</string>"
OFFSET=0
if [ "$ROLE" = backup ]; then ARGS="$ARGS<string>--backup</string>"; OFFSET=7; fi
# launchd fires in the Mac's local time zone; convert the Eastern times.
INTERVALS=$(/usr/bin/python3 - "$OFFSET" $TIMES_ET <<'PY'
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
offset = int(sys.argv[1])
today = datetime.now().date()
for t in sys.argv[2:]:
    h, m = map(int, t.split(":"))
    et = datetime(today.year, today.month, today.day, h, m, tzinfo=ZoneInfo("America/New_York")) + timedelta(minutes=offset)
    local = et.astimezone()
    print(f"<dict><key>Hour</key><integer>{local.hour}</integer><key>Minute</key><integer>{local.minute}</integer></dict>")
PY
)
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>com.aifurman.economist-wib</string>
<key>ProgramArguments</key><array>$ARGS</array>
<key>StartCalendarInterval</key><array>$INTERVALS</array>
<key>StandardOutPath</key><string>$HOME/Library/Logs/economist-wib.log</string>
<key>StandardErrorPath</key><string>$HOME/Library/Logs/economist-wib.log</string>
</dict></plist>
PL
launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "installed $ROLE: $(grep -o '<integer>[0-9]*</integer>' "$PLIST" | sed 's/<[^>]*>//g' | paste -sd' ' -)"
