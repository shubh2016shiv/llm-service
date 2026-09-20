"""
Cache invalidation channels
===========================

What is a "channel"?
    Redis can act like a radio. One program "publishes" (broadcasts) a
    message on a channel — a named radio frequency — and every other program
    that is "subscribed" (tuned in) to that same channel receives a copy.
    The sender never knows who is listening, and listeners never call the
    sender. It is pure broadcasting.

Why this file exists:
    A channel is just a piece of text, like ``"config:changes"``. The sender
    and every listener must spell that text EXACTLY the same, or messages
    are lost in silence: Redis never complains that nobody was listening.

    The danger is that the sender and the listeners live in different
    programs that cannot check each other's spelling. A typo or a rename on
    one side silently stops delivery — no import breaks, no test fails.
    Writing each channel name down ONCE in this file, and importing it from
    both sides, turns that silent breakage into a loud failure (a broken
    import or a failing test). One source of truth, no typos.

Why this file has no imports:
    Services that only want to ANNOUNCE something should not be forced to
    import the whole Redis package just to get a channel name. This file
    depends on nothing, so importing it is always cheap and safe.

Author: Shubham Singh
"""

# "Final" tells the type checker: this value is a constant. Nobody is
# allowed to change it after startup, and tools will warn if they try.
from typing import Final

# The one shared channel name for "configuration changed" announcements.
CONFIG_CHANGES_CHANNEL: Final = "config:changes"
"""Announcements that a configuration record changed.

What each message carries:
    The cache key that was just deleted (for example the key of a deployment
    config). The management service deletes the key AND announces it, in
    that order.

Why nobody inside this app listens to this channel today (on purpose):
    Redis is shared by every running copy of the app, so deleting a key in
    Redis already updates all copies at once — the announcement is not
    strictly needed yet. The channel exists for the future: if we ever add
    a small private in-memory cache per copy (a local notebook sitting in
    front of the shared Redis), those copies will subscribe to this channel
    to learn which notes to throw away. Use ``RedisCache.subscribe`` when
    that day comes.
"""
