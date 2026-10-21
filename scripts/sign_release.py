"""Sign a draft GitHub release with the offline Ed25519 key, then publish it.

    python scripts/sign_release.py --new-key      create the key once; paste the printed hex into updates.PUBLIC_KEY
    python scripts/sign_release.py v0.2.0         sign the draft's installer, upload the .sig, publish

The private key never leaves this computer. Back it up (for example in a password manager): without it, users must
reinstall manually once to trust a new key.
"""
import argparse
from pathlib import Path
import subprocess
import sys
import tempfile

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (Encoding, NoEncryption, PrivateFormat, PublicFormat,
                                                          load_pem_private_key)

from arabic_relay import updates

KEY = Path.home() / '.arabic-relay' / 'release-signing.key'


def new_key(path):
    if path.exists():
        raise FileExistsError(f'{path} already exists; it is never overwritten.')
    path.parent.mkdir(parents=True, exist_ok=True)
    key = Ed25519PrivateKey.generate()
    with path.open('xb') as out:
        out.write(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    return key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()


def sign(file, key_path):
    key = load_pem_private_key(Path(key_path).read_bytes(), None)
    return key.sign(Path(file).read_bytes())


def gh(*args):
    subprocess.run(['gh', *args], check=True)


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('tag', nargs='?')
    parser.add_argument('--new-key', action='store_true')
    parser.add_argument('--key', default=str(KEY))
    args = parser.parse_args(argv)
    if args.new_key:
        print(new_key(Path(args.key)))
        print(f'Key saved to {args.key}. Back it up now.', file=sys.stderr)
        return 0
    if not args.tag:
        parser.error('give a release tag, for example v0.2.0')
    with tempfile.TemporaryDirectory() as folder:
        gh('release', 'download', args.tag, '-p', 'ArabicRelay-Setup-*.exe', '-D', folder)
        found = list(Path(folder).glob('ArabicRelay-Setup-*.exe'))
        if len(found) != 1:
            print(f'Expected one installer in {args.tag}, found {len(found)}.', file=sys.stderr)
            return 1
        setup = found[0]
        signature = sign(setup, args.key)
        if not updates.verify(setup, signature):
            print('This key does not match updates.PUBLIC_KEY built into the app. Nothing was published.', file=sys.stderr)
            return 1
        signature_file = setup.with_name(setup.name + '.sig')
        signature_file.write_bytes(signature)
        gh('release', 'upload', args.tag, str(signature_file), '--clobber')
        gh('release', 'edit', args.tag, '--draft=false')
    print(f'{args.tag} is signed and published.')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
