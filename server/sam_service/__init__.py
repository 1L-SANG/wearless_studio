"""SAM2 garment segmentation service.

Separate deployment unit. torch, transformers and the SAM2 weights live ONLY here — the main
backend (`app/`) must never import from this package at runtime.
"""
