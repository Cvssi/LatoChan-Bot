"""One-shot decoder. No token in environment; caller enforces wall time and output size."""
import base64
import io
import json
import resource
import sys


def decode(data):
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = 25_000_000
    output, pixels = [], 0
    with Image.open(io.BytesIO(data), formats=['PNG', 'JPEG', 'GIF', 'WEBP']) as image:
        # Only decode the first three frames; never scan an entire animation.
        for index in range(3):
            try:
                image.seek(index)
            except EOFError:
                break
            area = image.width * image.height
            pixels += area
            if area > 25_000_000 or pixels > 50_000_000:
                raise ValueError('image pixel budget exceeded')
            with image.convert('RGB') as frame:
                frame.thumbnail((768, 768))
                with io.BytesIO() as buf:
                    frame.save(buf, 'JPEG', quality=85)
                    output.append(base64.b64encode(buf.getvalue()).decode('ascii'))
    return output


def main():
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024**2, 512 * 1024**2))
    resource.setrlimit(resource.RLIMIT_CPU, (6, 6))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    data = sys.stdin.buffer.read(15_000_001)
    if len(data) > 15_000_000:
        raise ValueError('image byte budget exceeded')
    json.dump(decode(data), sys.stdout)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        sys.exit(1)
