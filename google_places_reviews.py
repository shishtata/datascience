import os
import googlemaps
import pandas as pd
from dotenv import load_dotenv

# helper to normalise weird symbols
def clean_text(txt):
    if not txt:
        return ""
    # re-encode/decode to strip weird cp1252/utf mismatches
    return txt.encode("utf-8", errors="ignore").decode("utf-8")

# load API key from .env
load_dotenv()
API_KEY = os.getenv("GOOGLE_API_KEY")
gmaps = googlemaps.Client(key=API_KEY)

# attractions to fetch
places = [
    # Regional / National Parks
    ("Uluru-Kata Tjuta National Park, Northern Territory", "regional"),
    ("Kata Tjuta (The Olgas), Northern Territory", "regional"),
    ("Kings Canyon, Northern Territory", "regional"),
    ("Kakadu National Park, Northern Territory", "regional"),
    ("Nourlangie Rock, Kakadu National Park, Northern Territory", "regional"),
    ("Ubirr Rock Art Site, Kakadu National Park, Northern Territory", "regional"),
    ("Jim Jim Falls, Kakadu National Park, Northern Territory", "regional"),
    ("Litchfield National Park, Northern Territory", "regional"),
    ("Florence Falls, Litchfield National Park, Northern Territory", "regional"),
    ("Nitmiluk (Katherine Gorge) National Park, Northern Territory", "regional"),

    # Darwin city
    ("Darwin Waterfront Precinct, Darwin, Northern Territory", "city"),
    ("Mindil Beach Sunset Market, Darwin, Northern Territory", "city"),
    ("Museum and Art Gallery of the Northern Territory, Darwin", "city"),
    ("Crocosaurus Cove, Darwin, Northern Territory", "city"),
    ("Darwin Botanic Gardens, Northern Territory", "city"),
    ("George Brown Darwin Botanic Gardens, Darwin", "city"),
    ("Fannie Bay Gaol, Darwin, Northern Territory", "city"),
    ("Defence of Darwin Experience, Darwin, Northern Territory", "city"),
    ("Darwin Military Museum, Darwin, Northern Territory", "city"),
    ("Wave Lagoon, Darwin Waterfront, Northern Territory", "city"),
]


all_reviews = []

for place_name, track in places:
    result = gmaps.places(query=place_name)
    if result['status'] == 'OK' and result['results']:
        place_id = result['results'][0]['place_id']
        details = gmaps.place(place_id=place_id, fields=['name', 'rating', 'review'])

        reviews = details['result'].get('reviews', [])
        for r in reviews:
            all_reviews.append({
                "track": track,
                "source": "Google Places",
                "attraction": details['result']['name'],
                # 👇 use clean_text here
                "review_text": clean_text(r.get('text', '')),
                "rating": r.get('rating', None),
                "review_date": r.get('relative_time_description', ''),
                "reviewer_origin": "",  # not provided by API
                "lat": "",
                "lon": "",
                "url": ""
            })
        print(f"✅ Got {len(reviews)} reviews for {details['result']['name']}")
    else:
        print(f"❌ No results found for {place_name}")

# save to CSV
df = pd.DataFrame(all_reviews)
out_path = "data/nt_reviews.csv"
df.to_csv(out_path, index=False, encoding="utf-8")
print(f"\n💾 Saved {len(df)} total reviews to {out_path}")
