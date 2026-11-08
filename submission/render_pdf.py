import sys, os
import pymupdf

pdf = sys.argv[1]
outdir = sys.argv[2]
os.makedirs(outdir, exist_ok=True)
doc = pymupdf.open(pdf)
print("pages:", doc.page_count)
for i, page in enumerate(doc):
    pix = page.get_pixmap(dpi=130)
    path = os.path.join(outdir, f"page{i+1}.png")
    pix.save(path)
    print(path, pix.width, pix.height)
