"""A stand-in for FlexGEMM, which MoGe-3's code imports at the top but runs only in its refinement.

This runtime builds MoGe-3 without its refiner (refine_steps=0), so nothing here is ever called;
anything that is raises, so a refinement that runs by mistake fails loudly instead of giving another
model's result. FlexGEMM itself is built on Triton for cards of compute 8.0 and newer, and comes
with a report from such a card (spec 005).
"""

from . import nn, ops

__all__ = ["nn", "ops"]
