# The test CA

The hosts in `../hosts.py` serve TLS with these certificates, so spec 003's downloads are tested
over TLS on every system, with nothing installed in the machine's trust store:

- `ca.pem`: the test CA. A client in a test trusts it, and only it.
- `leaf.pem` and `leaf.key`: the hosts' certificate, signed by the CA, for `127.0.0.1` and
  `localhost`.
- `other-ca.pem`: a second CA that signed nothing here. A client that trusts only it refuses every
  host, which is how a test meets a certificate that fails.

All are P-256 keys, valid from 2026-01-01 to 2126-01-01, and pass Python's strict checks
(`VERIFY_X509_STRICT`, on by default since 3.13): each CA has a critical `basicConstraints`
and `keyUsage` and a subject key id; the leaf has a critical `CA:FALSE` and `digitalSignature`,
`serverAuth`, its names in `subjectAltName`, and both key ids. The CA's private key was not kept:
the leaf outlives every test, and a new set is made rather than a new leaf.

The standard library cannot make certificates, so they were made once with `cryptography`, by a
script kept out of the repo so that no check imports it:

```python
# uv run --no-project --python 3.14 --with cryptography make_certs.py <folder>
import datetime, ipaddress, sys
from pathlib import Path
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

out = Path(sys.argv[1])
start = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
end = datetime.datetime(2126, 1, 1, tzinfo=datetime.UTC)
name = lambda text: x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, text)])
pem = lambda cert: cert.public_bytes(serialization.Encoding.PEM)


def ca(text):
    key = ec.generate_private_key(ec.SECP256R1())
    cert = (
        x509.CertificateBuilder()
        .subject_name(name(text))
        .issuer_name(name(text))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(start)
        .not_valid_after(end)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    return key, cert


ca_key, ca_cert = ca("Oneframe Lab test CA")
_, other = ca("Oneframe Lab untrusted test CA")
leaf_key = ec.generate_private_key(ec.SECP256R1())
leaf = (
    x509.CertificateBuilder()
    .subject_name(name("127.0.0.1"))
    .issuer_name(ca_cert.subject)
    .public_key(leaf_key.public_key())
    .serial_number(x509.random_serial_number())
    .not_valid_before(start)
    .not_valid_after(end)
    .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
    .add_extension(x509.KeyUsage(True, False, False, False, False, False, False, False, False), critical=True)
    .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
    .add_extension(
        x509.SubjectAlternativeName(
            [x509.IPAddress(ipaddress.ip_address("127.0.0.1")), x509.DNSName("localhost")]
        ),
        critical=False,
    )
    .add_extension(x509.SubjectKeyIdentifier.from_public_key(leaf_key.public_key()), critical=False)
    .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
    .sign(ca_key, hashes.SHA256())
)
(out / "ca.pem").write_bytes(pem(ca_cert))
(out / "other-ca.pem").write_bytes(pem(other))
(out / "leaf.pem").write_bytes(pem(leaf))
(out / "leaf.key").write_bytes(
    leaf_key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
)
```
