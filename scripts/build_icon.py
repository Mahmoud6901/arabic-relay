"""Package the approved PNG as a multi-resolution Windows icon (requires Pillow for asset work only)."""
from pathlib import Path
import argparse

from PIL import Image, PngImagePlugin


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--prompt-file', type=Path, required=True)
    args = parser.parse_args()
    assets = Path(__file__).resolve().parents[1] / 'src/arabic_relay/assets'
    image = Image.open(args.source).convert('RGBA')
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text('Source', 'Generated with OpenAI image generation; approved lime-and-charcoal relay direction.')
    metadata.add_text('Prompt', args.prompt_file.read_text(encoding='utf-8'))
    image.resize((512, 512), Image.Resampling.LANCZOS).save(assets / 'icon.png', pnginfo=metadata)
    image.save(assets / 'relay.ico', sizes=[(size, size) for size in (16, 20, 24, 32, 40, 48, 64, 128, 256)])
    print('Packaged icon.png and relay.ico (16–256 px) with transparent corners.')


if __name__ == '__main__':
    main()
