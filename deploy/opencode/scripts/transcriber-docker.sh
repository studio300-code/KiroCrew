#!/bin/sh
# Container-side client for the host's resident orukeet server.
#
# The ASUS GPU has 2 GB: a second resident model inside the container would not
# fit, so the container shares the host socket. The audio is staged in a shared
# cache directory that is bind-mounted at the same absolute path on both sides;
# the host server reads it and writes the .txt/.srt there, and this wrapper
# copies the result back into the media directory.
set -eu

SHARED=${ORUKEET_SHARED_DIR:-/home/marco/.cache/orukeet-shared}
CLIENT=${ORUKEET_CLIENT:-/home/marco/.local/share/orukeet-native/orukeet_client.py}
SOCKET=${ORUKEET_SOCKET:-/home/marco/.cache/orukeet-transcriber.sock}

LANG_OPT=it
OUTDIR=""
IN=""
while [ "$#" -gt 0 ]; do
    case "$1" in
        --lang) LANG_OPT=${2:-it}; shift 2 ;;
        --model) shift 2 ;;
        --out) OUTDIR=${2:-}; shift 2 ;;
        --fast) shift ;;
        --) shift; IN=${1:-}; shift ;;
        -*) shift ;;
        *) IN=$1; shift ;;
    esac
done

[ -n "$IN" ] || { echo "no input file" >&2; exit 2; }
[ -f "$IN" ] || { echo "not found: $IN" >&2; exit 2; }
[ -S "$SOCKET" ] || { echo "orukeet resident socket not available: $SOCKET" >&2; exit 5; }
[ -f "$CLIENT" ] || { echo "orukeet client missing: $CLIENT" >&2; exit 5; }

DEST=${OUTDIR:-$(dirname "$IN")}
mkdir -p "$DEST" "$SHARED"
TMP=$(mktemp -d "$SHARED/in-XXXXXX")
trap 'rm -rf "$TMP"' EXIT INT TERM

ffmpeg -y -v error -i "$IN" -vn -ac 1 -ar 16000 -sample_fmt s16 "$TMP/in.wav"
# The client also prints TXT=/SRT= for the shared copy. Keep stdout clean so
# the archive worker reads the final media path printed below, not a temp file
# the trap is about to delete.
ORUKEET_SOCKET="$SOCKET" python3 "$CLIENT" "$TMP/in.wav" --out-prefix "$TMP/out" >/dev/null

[ -s "$TMP/out.txt" ] || { echo "orukeet returned an empty transcript for $IN (no fallback)" >&2; exit 4; }

BASE=$(basename "$IN")
BASE=${BASE%.*}
STAMP="$BASE.$LANG_OPT.orukeet"
cp "$TMP/out.txt" "$DEST/$STAMP.txt"
cp "$TMP/out.srt" "$DEST/$STAMP.srt"
echo "TXT=$DEST/$STAMP.txt"
echo "SRT=$DEST/$STAMP.srt"
