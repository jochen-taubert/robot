#!/bin/bash
# set_audio_levels.sh
#
# Setzt Lautsprecher- (card 1, Kopfhörerbuchse) und Mikrofon-Lautstärke (card 2, USB-Mikrofon)
# und installiert einen systemd-Service, der diese Werte bei jedem Neustart automatisch
# wieder setzt (unabhängig vom fehleranfälligen alsa-restore.service).
#
# Anpassen falls nötig:
#   - Kartennummern (aktuell: Speaker=1, Mic=2) mit `cat /proc/asound/cards` prüfen
#   - Regler-Namen (aktuell: PCM, Mic) mit `amixer -c <karte> scontrols` prüfen
#
# Ausführen mit: sudo bash set_audio_levels.sh

set -e

SPEAKER_CARD=1
SPEAKER_CONTROL="PCM"
SPEAKER_LEVEL="100%"

MIC_CARD=2
MIC_CONTROL="Mic"
MIC_LEVEL="90%"

if [ "$EUID" -ne 0 ]; then
    echo "Bitte mit sudo ausführen: sudo bash $0"
    exit 1
fi

echo "Setze Lautsprecher (card ${SPEAKER_CARD}, ${SPEAKER_CONTROL}) auf ${SPEAKER_LEVEL} ..."
amixer -c "$SPEAKER_CARD" sset "$SPEAKER_CONTROL" "$SPEAKER_LEVEL"

echo "Setze Mikrofon (card ${MIC_CARD}, ${MIC_CONTROL}) auf ${MIC_LEVEL} ..."
amixer -c "$MIC_CARD" sset "$MIC_CONTROL" "$MIC_LEVEL"

echo "Installiere systemd-Service für Persistenz über Neustarts hinweg ..."

tee /etc/systemd/system/audio-levels.service > /dev/null << EOF
[Unit]
Description=Set speaker and microphone volume on boot
After=sound.target alsa-restore.service
Requires=alsa-restore.service

[Service]
Type=oneshot
ExecStartPre=/bin/sleep 8
ExecStart=/usr/bin/amixer -c ${SPEAKER_CARD} sset ${SPEAKER_CONTROL} ${SPEAKER_LEVEL}
ExecStart=/usr/bin/amixer -c ${MIC_CARD} sset ${MIC_CONTROL} ${MIC_LEVEL}

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable audio-levels.service
systemctl start audio-levels.service

echo ""
echo "Fertig. Aktuelle Werte:"
amixer -c "$SPEAKER_CARD" sget "$SPEAKER_CONTROL"
amixer -c "$MIC_CARD" sget "$MIC_CONTROL"

echo ""
echo "Test nach Neustart mit:"
echo "  sudo reboot"
echo "  amixer -c ${SPEAKER_CARD} sget ${SPEAKER_CONTROL}"
echo "  amixer -c ${MIC_CARD} sget ${MIC_CONTROL}"
