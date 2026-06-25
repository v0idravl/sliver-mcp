"""Vendored, renamed protobuf message types for implant generation.

Why this exists: the official ``sliver-py`` client bundles its own protobuf
stubs, and as of 0.0.19 those predate the ``HTTPC2ConfigName`` field (number
150) that the Sliver v1.7.x server's ``Generate`` RPC requires — so sliver-py
literally cannot build a valid implant request for a current server.

Rather than pin to an old server, we compile the *target* Sliver's
``commonpb``/``clientpb`` protos here, renamed to ``mcpcommonpb``/``mcpclientpb``
so they register under distinct symbols and never collide with sliver-py's stubs
in the global descriptor pool. Implant generation builds these messages and
sends them over sliver-py's already-authenticated gRPC channel
(:meth:`sliver_mcp.manager.SliverManager.generate_implant`).

To regenerate against a different Sliver version, see ``scripts/build_pb.sh``.

The generated modules import each other by top-level package name
(``from mcpcommonpb import common_pb2``), so this directory is added to
``sys.path`` on import.
"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(__file__)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from mcpclientpb import client_pb2  # noqa: E402
from mcpcommonpb import common_pb2  # noqa: E402

__all__ = ["client_pb2", "common_pb2"]
