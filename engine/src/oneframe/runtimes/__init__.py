"""Runtimes: the Python environments that heavy nodes run in, one per node family.

A runtime is data, a folder the engine finds by looking (`runtimes/<id>/` in the repo):

    pyproject.toml   a virtual uv project; each build (cpu, cu126, cu130, ...) is an extra, and
                     the extras conflict, so one lock holds every build's packages
    uv.lock          committed; what `uv sync --frozen` installs, exactly
    runtime.json     the builds and what each needs from the machine, the Python, compiled
                     extensions and their class, pinned upstream sources, environment variables

Nothing in the engine names a runtime. A node names the runtime it runs in (`run.runtime` in its
manifest), and the scheduler asks the manager for that runtime's interpreter.

- `definition`: reads and checks definitions, pure;
- `plan`: picks the build for a machine, pure;
- `manager`: each runtime's state here, install and remove, and what the scheduler asks;
- `install`: builds one runtime's environment, the only part that fetches anything.

Callers import the module they need. Nothing is re-exported here, so planning loads neither the
manager nor the install.
"""
