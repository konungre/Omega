#!/usr/bin/env python3
"""Fetch the pinned SmolLM3 GGUF once, verifying the published SHA-256."""
import argparse
import hashlib
from pathlib import Path
import time
import urllib.request

MODEL_REVISION = '4965cb60b150737b68a0408c36aeefb65078f894'
MODEL_SHA256 = '8334b850b7bd46238c16b0c550df2138f0889bf433809008cc17a8b05761863e'
MODEL_URL = f'https://huggingface.co/ggml-org/SmolLM3-3B-GGUF/resolve/{MODEL_REVISION}/SmolLM3-Q4_K_M.gguf'


def checksum(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(8 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def fetch(target):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file() and checksum(target) == MODEL_SHA256:
        print('SmolLM3 model already verified', flush=True)
        return
    temporary = target.with_suffix('.part')
    for attempt in range(3):
        try:
            digest, total = hashlib.sha256(), 0
            request = urllib.request.Request(MODEL_URL, headers={'User-Agent': 'Omega-model-installer/1'})
            with urllib.request.urlopen(request, timeout=90) as response, temporary.open('wb') as output:
                while block := response.read(8 * 1024 * 1024):
                    output.write(block)
                    digest.update(block)
                    total += len(block)
                    if total % (256 * 1024 * 1024) < len(block):
                        print(f'SmolLM3 download: {total // (1024 * 1024)} MiB', flush=True)
            if digest.hexdigest() != MODEL_SHA256:
                raise RuntimeError('SmolLM3 SHA-256 mismatch; refusing to install an unverified model')
            temporary.replace(target)
            print(f'SmolLM3 installed and verified: {total} bytes', flush=True)
            return
        except Exception:
            temporary.unlink(missing_ok=True)
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('target', nargs='?', default='/opt/omega/models/SmolLM3-Q4_K_M.gguf')
    fetch(parser.parse_args().target)
