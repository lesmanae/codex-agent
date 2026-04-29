# Cryptography & Token Analysis — Skill

**Trigger phrases**: jwt, jws, jwe, jwk, jwks, token decode, oauth, oidc, openid,
oauth2, refresh token, bearer token, hmac, sha256, sha512, md5, sha1, blake2,
hash, rainbow table, crc32, base64, base32, base58, hex, rot13, caesar, ascii,
encoding, decoding, aes, aes-gcm, aes-cbc, des, 3des, chacha20, rsa, ecdsa,
ed25519, ecdh, dh, x25519, ssh key, gpg, pgp, pem, pkcs8, pkcs12, p12, der,
csr, x509, certificate, ssl certificate, tls, tls1.3, openssl, gnutls,
fingerprint, pubkey, privkey, sign, verify, encrypt, decrypt, signature,
hsm, pkcs11, hkdf, pbkdf2, bcrypt, scrypt, argon2, password hash, secret key,
key derivation, kdf, nonce, iv, salt, entropy, csrng, urandom.

Use when the user asks to inspect, decode, generate, or verify
cryptographic material — JWTs, certificates, signatures, hashes, keys,
encrypted blobs.

---

## Operating principles

- **Decode is not verify.** Reading a JWT payload requires no key.
  Verifying it requires the public key + algorithm match.
- **Don't paste real production secrets to chat.** Mask them. Operate on
  files in `/tmp/`.
- **Use `openssl` and `step-cli` for cert work.** They speak every
  format.
- **Always prefer authenticated encryption (AES-GCM, ChaCha20-Poly1305)
  over unauthenticated (AES-CBC alone).**
- **Never roll your own crypto.** If the user asks "encrypt with
  algorithm X", reach for a battle-tested library.

---

## JWT / OIDC

### Decode header + payload (no signature check)

```bash
python3 -c "
import sys, json, base64
def b64d(s): s += '=' * (-len(s) % 4); return base64.urlsafe_b64decode(s)
tok = sys.argv[1]
h, p, _ = tok.split('.')
print(json.dumps(json.loads(b64d(h)), indent=2))
print('---')
print(json.dumps(json.loads(b64d(p)), indent=2))
" "$JWT"
```

Or one-liner:
```bash
echo "$JWT" | cut -d. -f2 | tr '_-' '/+' | base64 -d 2>/dev/null | jq
```

### Verify a JWT signature

```bash
# RS256 — fetch JWKS, pick kid, verify
JWKS=$(curl -s https://issuer.example/.well-known/jwks.json)
KID=$(echo "$JWT" | cut -d. -f1 | tr '_-' '/+' | base64 -d 2>/dev/null | jq -r .kid)
PEM=$(echo "$JWKS" | jq -r ".keys[] | select(.kid==\"$KID\")" | step crypto jwk format --pem)
python3 - <<PY
import jwt, sys
print(jwt.decode("$JWT", "$PEM", algorithms=["RS256"]))
PY
```

`step` (smallstep) and `python -m jwt` make this fast.

### Mint a quick HS256 JWT for testing

```bash
python3 -c "
import jwt, time
print(jwt.encode({'sub':'alice','exp':int(time.time())+3600}, 'secret', algorithm='HS256'))
"
```

---

## OpenSSL — every cert task

```bash
# inspect a certificate file
openssl x509 -in cert.pem -noout -text

# show cert fields succinctly
openssl x509 -in cert.pem -noout -subject -issuer -dates -fingerprint -ext subjectAltName

# inspect the live cert of a host (incl. SAN)
openssl s_client -connect host:443 -servername host -showcerts </dev/null \
  | openssl x509 -noout -subject -issuer -dates -ext subjectAltName

# check cert against a private key (must match)
diff \
  <(openssl x509 -in cert.pem -noout -modulus | openssl md5) \
  <(openssl rsa -in key.pem -noout -modulus | openssl md5)

# inspect a CSR
openssl req -in req.csr -noout -text -verify

# convert formats
openssl x509 -in cert.pem -outform DER -out cert.der
openssl pkcs12 -export -in cert.pem -inkey key.pem -out bundle.p12 -name alice
openssl pkcs12 -in bundle.p12 -out unbundle.pem -nodes
```

## Generate keypairs

```bash
# RSA 4096
openssl genrsa -out priv.pem 4096
openssl rsa -in priv.pem -pubout -out pub.pem

# Ed25519 (preferred for new code)
openssl genpkey -algorithm ed25519 -out ed_priv.pem
openssl pkey -in ed_priv.pem -pubout -out ed_pub.pem

# SSH
ssh-keygen -t ed25519 -C "alice@host" -f ~/.ssh/id_ed25519 -N ""
```

## Sign + verify a file

```bash
openssl dgst -sha256 -sign priv.pem -out msg.sig msg.txt
openssl dgst -sha256 -verify pub.pem -signature msg.sig msg.txt
# → "Verified OK"
```

## Hash a file / string

```bash
sha256sum file
sha512sum file
md5sum file       # never use for security; only checksums

echo -n "hello" | sha256sum
echo -n "hello" | openssl dgst -sha3-256
```

## Encode/decode

```bash
echo -n "hello" | base64               # encode
echo "aGVsbG8=" | base64 -d            # decode
echo -n "hello" | xxd -p               # hex
echo "68656c6c6f" | xxd -r -p          # un-hex
```

## Symmetric encryption (AES-256-GCM via openssl)

```bash
KEY=$(openssl rand -hex 32)            # 256-bit
IV=$(openssl rand -hex 12)             # 96-bit nonce
openssl enc -aes-256-gcm -K $KEY -iv $IV -in plain.txt -out cipher.bin
openssl enc -aes-256-gcm -K $KEY -iv $IV -d -in cipher.bin -out plain.txt.dec
```

For real applications, prefer libsodium (`from nacl.secret import SecretBox`)
or `cryptography` (`AESGCM` from `cryptography.hazmat`) — they handle
nonce + tag for you.

---

## Password hashing (server-side)

| Use | Algorithm |
|---|---|
| User passwords (default) | argon2id |
| User passwords (legacy) | bcrypt |
| KDF from password (file/disk) | scrypt or argon2id |

```python
from argon2 import PasswordHasher
ph = PasswordHasher()
hash_ = ph.hash("user-password")
ph.verify(hash_, "user-password")     # raises if wrong
```

NEVER store passwords as `sha256(password)` — too fast, rainbow-tableable.

---

## Common JWT pitfalls

- Algorithm `none` accepted by old libraries. Always pin
  `algorithms=["RS256"]` in verifier.
- HS256 with public-key-as-secret: attacker can sign their own with the
  RSA public key as the HMAC secret. Whitelist algorithm + key type.
- `exp` not validated. Most libs do, but `verify_exp=True` must be on.
- `aud` and `iss` mismatched between issuer and validator.
