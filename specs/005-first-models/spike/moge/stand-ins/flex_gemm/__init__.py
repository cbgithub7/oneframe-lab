"""A stand-in for FlexGEMM, which MoGe-3's code imports at the top but runs only in its refinement.

The spike builds MoGe-3 without its refiner, so nothing here is ever called; anything that is
raises, so a refinement that runs by mistake fails loudly instead of giving another model's result.
"""

from . import nn, ops

__all__ = ["nn", "ops"]
