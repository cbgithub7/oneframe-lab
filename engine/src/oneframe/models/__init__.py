"""The model store (spec 003): the files nodes read, declared in their manifests, downloaded by a
job that heals itself, kept once each by sha256 under the data root's `models/`, and handed to a
run only once checked.

Nothing here is imported when the engine starts but the schema, which the manifest check needs;
the transport and its TLS library load when a download starts.
"""
