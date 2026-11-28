"""Stark World Engine Systems: data-driven gameplay systems for Stark World Engine (JS runtime modules + Python validators,
generators and the child-safety export gate). See SYSTEMS_GUIDE.md, CHILD_SAFETY.md and INTEGRATION_PATCHES.md.

The world.json extension lives under world['systems'] (schema version SCHEMA_VERSION, every field optional, worlds
without it load unchanged). Child safety is not optional: child_safety.enforce_export() must run before any export.
"""
__version__ = '1.0.0'
SCHEMA_VERSION = 1
