"""Fit to memory (spec 002): what a node needs, where it runs, and what each machine learns.

- `model`: a node's memory model, data in its manifest, and its parser; reading a manifest loads
  this module alone of the five;
- `settings`: the person's settings for the fit, and the margins;
- `estimate`: what a run needs on a device;
- `fit`: where a node runs and at which values, before its model loads;
- `learned`: what each machine learns, kept under the data root.

Callers import the module they need. Nothing is re-exported here, so a light module stays light.
"""
