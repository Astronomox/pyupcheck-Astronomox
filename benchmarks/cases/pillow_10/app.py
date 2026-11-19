from PIL import Image, ImageDraw

img = Image.new("RGB", (10, 10))
small = img.resize((5, 5), Image.ANTIALIAS)  # BREAK
ok = img.resize((5, 5), Image.LANCZOS)
draw = ImageDraw.Draw(img)
w, h = draw.textsize("hi")  # BREAK
draw.text((0, 0), "hi")
