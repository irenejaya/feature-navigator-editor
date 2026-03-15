# -*- coding: utf-8 -*-
"""
FeatureNavEd - QGIS Plugin

Navigate and edit features of a vector layer one by one with attribute display.
Provides a dockable panel with previous/next navigation, sorting, and auto-zoom.
"""

__author__ = 'Irene Jaya'
__date__ = '2026-03-14'
__copyright__ = '(C) 2026, Irene Jaya'


def classFactory(iface):
    from .plugin import FeatureNavEdPlugin
    return FeatureNavEdPlugin(iface)
