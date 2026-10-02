# Icône de l'application

Le fichier binaire `icon.ico` n'est pas versionné ici (fichier binaire).
Pour le régénérer :

```python
from PIL import Image, ImageDraw
img = Image.new('RGBA', (256,256), (22,24,29,255))
d = ImageDraw.Draw(img)
d.rounded_rectangle([28,28,228,228], radius=48, fill=(43,108,176,255))
d.rounded_rectangle([118,60,138,140], radius=10, fill=(255,255,255,255))
d.rounded_rectangle([108,120,148,180], radius=14, fill=(255,255,255,255))
d.rounded_rectangle([96,180,160,196], radius=8, fill=(255,255,255,255))
img.save('assets/icon.ico', sizes=[(256,256),(64,64),(48,48),(32,32),(16,16)])
```
