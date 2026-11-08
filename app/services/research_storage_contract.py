"""Installed storage identifiers, independent of application/file names.

These opaque values match existing MySQL CHECK constraints and signed formats.
They do not expose alternate collection methods or select an older runtime.
Changing them requires a reviewed schema transition, not a source-file rename.
"""
COLLECTION_SCHEMA = "natural-use-events.v2"
COLLECTION_PROTOCOL = "natural-use-protocol.v2"
FEATURE_SCHEMA = "recent-frustration-features.v1"
DATASET_FORMAT = "natural-use-v2-export.v1"
QUALITY_SCHEMA = "research-quality.v1"
INSTALLED_CONFIGURATION_SCHEMAS = ("natural-use-events.v1", COLLECTION_SCHEMA)
