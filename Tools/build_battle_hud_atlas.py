"""Lossless deterministic HUD packing. Requires Pillow; rerun when HUD source art changes."""
from pathlib import Path
import json, uuid
from PIL import Image

root = Path(__file__).resolve().parents[1]
folder = root / 'Assets/ZZZ/Arts/UI/HUD'
catalog_path = root / 'Assets/Bundles/Config/Vfx/BattleAssetCatalog.asset'
catalog = json.loads(catalog_path.read_text(encoding='utf-8-sig'))
names = catalog['HudNames']['$values']
page = Image.new('RGBA', (512, 512))
rects, x, y, row = [], 2, 2, 0
for name in names:
    source = Image.open(folder / name).convert('RGBA')
    w, h = source.size
    if x+w+2 > 512: x, y, row = 2, y+row+4, 0
    if y+h+2 > 512: raise ValueError('HUD atlas exceeded 512x512')
    page.paste(source, (x, y))
    # Two extruded pixels protect bilinear sampling at each sprite edge.
    for d in (1, 2):
        page.paste(source.crop((0,0,1,h)), (x-d,y))
        page.paste(source.crop((w-1,0,w,h)), (x+w-1+d,y))
        page.paste(source.crop((0,0,w,1)), (x,y-d))
        page.paste(source.crop((0,h-1,w,h)), (x,y+h-1+d))
    # SpriteRect is FixedEchoStructure: its wire format is an ordered four-int list.
    rects.append([x,512-y-h,w,h])
    assert page.crop((x,y,x+w,y+h)).tobytes() == source.tobytes()
    x += w+4; row=max(row,h)
atlas_path = folder / 'BattleHudAtlas.png'
page.save(atlas_path)
guid = str(uuid.uuid5(uuid.NAMESPACE_URL, 'xengine-zonezero/BattleHudAtlas'))
meta = dict(guid=guid, importer='TextureImporter', importerVersion=6,
    settings=dict(generateMipmaps=False,sRGB=True,sRGBSampling=True,minFilter=1,magFilter=1,wrapMode=1))
Path(str(atlas_path)+'.meta').write_text(json.dumps(meta,indent=2)+'\n',encoding='utf-8')
catalog['HudAtlas'] = dict(AssetID=guid)
catalog['HudAtlasRects'] = {'$values':rects}
catalog_path.write_text(json.dumps(catalog,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
print(f'{len(names)} sprites, 512x512 RGBA, all source pixels verified')
