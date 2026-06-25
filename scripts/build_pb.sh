#!/usr/bin/env bash
# Regenerate the vendored protobuf stubs used for implant generation.
#
# sliver-py's bundled stubs lag the Sliver server protobuf (see
# sliver_mcp/_pb/__init__.py). We compile commonpb + clientpb from the target
# Sliver source, renamed to mcpcommonpb / mcpclientpb so they don't collide with
# sliver-py's stubs, and send them over sliver-py's authenticated channel.
#
# Usage:  scripts/build_pb.sh [PATH_TO_SLIVER_SRC]   (default: ~/projects/sliver)
set -euo pipefail

SLIVER_SRC="${1:-$HOME/projects/sliver}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROTO_IN="$SLIVER_SRC/protobuf"
PROTO_OUT="$ROOT/proto"
PB_OUT="$ROOT/sliver_mcp/_pb"
PY="${PYTHON:-$ROOT/venv/bin/python}"

[ -f "$PROTO_IN/clientpb/client.proto" ] || { echo "no protos at $PROTO_IN" >&2; exit 1; }

mkdir -p "$PROTO_OUT/mcpcommonpb" "$PROTO_OUT/mcpclientpb" "$PB_OUT"

sed 's/^package commonpb;/package mcpcommonpb;/' \
    "$PROTO_IN/commonpb/common.proto" > "$PROTO_OUT/mcpcommonpb/common.proto"

sed -e 's/^package clientpb;/package mcpclientpb;/' \
    -e 's#import "commonpb/common.proto";#import "mcpcommonpb/common.proto";#' \
    -e 's/commonpb\./mcpcommonpb./g' \
    "$PROTO_IN/clientpb/client.proto" > "$PROTO_OUT/mcpclientpb/client.proto"

"$PY" -m grpc_tools.protoc -I"$PROTO_OUT" --python_out="$PB_OUT" \
    "$PROTO_OUT/mcpcommonpb/common.proto" "$PROTO_OUT/mcpclientpb/client.proto"

touch "$PB_OUT/mcpcommonpb/__init__.py" "$PB_OUT/mcpclientpb/__init__.py"
echo "regenerated vendored stubs in $PB_OUT"
