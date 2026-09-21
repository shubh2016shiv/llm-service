"""Reusable contracts and transport primitives for incremental output.

Architecture:
    producer -> StreamEventPayload -> SSEStreamDelivery -> HTTP framework

Import from the module that owns the responsibility. Avoiding eager barrel
exports keeps the protocol layer independent from application-specific chat
schemas and prevents hidden import cycles when this package is reused.
"""
