"""The model store (spec 003): the files nodes read, declared in their manifests, downloaded by a
job that heals itself, kept once each by sha256 under the data root's `models/`, and handed to a
run only once checked.

The engine imports the store, its jobs and the transport when it starts (a runtime's sources use
the transport too, spec 011); only truststore, the transport's TLS library, loads when a download
starts, and the pin and download tools only with their commands.
"""
