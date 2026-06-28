"""Download board advisor photos to static/advisors/"""
import urllib.request, os
from pathlib import Path

OUT = Path(__file__).parent.parent / "static" / "advisors"
OUT.mkdir(parents=True, exist_ok=True)

ADVISORS = {
    "elon":    "https://upload.wikimedia.org/wikipedia/commons/thumb/e/ed/Elon_Musk_Royal_Society_%28crop2%29.jpg/400px-Elon_Musk_Royal_Society_%28crop2%29.jpg",
    "bezos":   "https://upload.wikimedia.org/wikipedia/commons/thumb/6/6c/Jeff_Bezos_at_Amazon_Spheres_Grand_Opening_in_Seattle_-_2018_%2839074799225%29_%28cropped%29.jpg/400px-Jeff_Bezos_at_Amazon_Spheres_Grand_Opening_in_Seattle_-_2018_%2839074799225%29_%28cropped%29.jpg",
    "buffett": "https://upload.wikimedia.org/wikipedia/commons/thumb/5/51/Warren_Buffett_KU_Visit.jpg/400px-Warren_Buffett_KU_Visit.jpg",
    "jobs":    "https://upload.wikimedia.org/wikipedia/commons/thumb/b/b9/Steve_Jobs_Headshot_2010-CROP_%28cropped_2%29.jpg/400px-Steve_Jobs_Headshot_2010-CROP_%28cropped_2%29.jpg",
    "munger":  "https://upload.wikimedia.org/wikipedia/commons/thumb/3/34/Charlie_Munger_%28cropped%29.jpg/400px-Charlie_Munger_%28cropped%29.jpg",
    "thiel":   "https://upload.wikimedia.org/wikipedia/commons/thumb/b/bc/Peter_Thiel_Ira_Sohn_2014_Conference_%28cropped%29.jpg/400px-Peter_Thiel_Ira_Sohn_2014_Conference_%28cropped%29.jpg",
    "dalio":   "https://upload.wikimedia.org/wikipedia/commons/thumb/6/6f/Ray_Dalio_%28cropped%29.jpg/400px-Ray_Dalio_%28cropped%29.jpg",
}

HEADERS = {"User-Agent": "Mozilla/5.0 ThinkGalactic/1.0"}

for name, url in ADVISORS.items():
    dest = OUT / f"{name}.jpg"
    if dest.exists():
        print(f"  {name}: already downloaded")
        continue
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=15) as r:
            dest.write_bytes(r.read())
        print(f"  {name}: ✓ ({dest.stat().st_size // 1024}KB)")
    except Exception as e:
        print(f"  {name}: FAILED — {e}")

print("Done.")
