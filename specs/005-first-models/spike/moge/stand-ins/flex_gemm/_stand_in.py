"""What every stand-in class is: importable, never usable."""


class StandIn:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise RuntimeError(
            f"{type(self).__name__} is FlexGEMM's, which this environment does not have: "
            "MoGe-3 runs here without its refinement (refine_steps=0)."
        )


def stand_in(name: str) -> type:
    return type(name, (StandIn,), {})
