"""Processors turn a source's raw file into text segments that say where in the source they came from.

One processor per kind of content. Each returns segments at the source's natural boundaries - pages,
headings, time windows - so later stages can cite a page or a timestamp, not just a whole source.
"""
