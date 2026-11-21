from PIL import Image, ImageFont

img = Image.new("RGB", (4, 4))
a = img.resize((2, 2), Image.LINEAR)  # BREAK
b = img.resize((2, 2), Image.CUBIC)  # BREAK
c = img.resize((2, 2), Image.BILINEAR)


def measure(font: ImageFont.FreeTypeFont):
    return font.getsize("hi")  # BREAK


def measure_ok(font: ImageFont.FreeTypeFont):
    return font.getbbox("hi")
