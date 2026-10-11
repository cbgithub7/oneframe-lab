"""A stub: this node exists so `npm run models:pin` and `npm run models:download` have a manifest
to work on (spec 003, AC8). It pins and fetches files; it never runs."""


def run(ctx):
    raise RuntimeError("a live manifest of spec 003 only pins and fetches files; it does not run")
