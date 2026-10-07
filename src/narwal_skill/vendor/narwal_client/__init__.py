"""Read-only Narwal protocol helpers.

This package does not import or expose the upstream control client.
"""

from .map_renderer import MAP_RENDER_SCALE, decompress_map, render_map_png
from .models import MapData, MapDisplayData
from .protocol import PROTOBUF_FIELD5_TAG, NarwalMessage, build_frame, parse_frame

__all__ = [
    "MAP_RENDER_SCALE",
    "MapData",
    "MapDisplayData",
    "NarwalMessage",
    "PROTOBUF_FIELD5_TAG",
    "build_frame",
    "decompress_map",
    "parse_frame",
    "render_map_png",
]
