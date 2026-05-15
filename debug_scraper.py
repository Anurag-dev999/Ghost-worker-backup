import os
from dotenv import load_dotenv
from apify_client import ApifyClient

load_dotenv()

APIFY_API_TOKEN = os.getenv("APIFY_API_TOKEN")

client = ApifyClient(APIFY_API_TOKEN)

run_input = {
    "searchStringsArray":        ["Gyms in Lucknow"],
    "maxCrawledPlacesPerSearch": 5,
    "language":                  "en",
    "hasWebsite":                False,
    "exportPlaceUrls":           False,
    "includeHistogram":          False,
    "includeOpeningHours":       False,
    "includePeopleAlsoSearch":   False,
    "additionalInfo":            False,
}

print("Fetching 5 raw results from Apify...\n")
run   = client.actor("compass/crawler-google-places").call(run_input=run_input)
items = list(client.dataset(run["defaultDatasetId"]).iterate_items())

for i, place in enumerate(items, 1):
    print(f"─── Lead {i} ───────────────────────────────")
    print(f"  Name     : {place.get('title')}")
    print(f"  Phone    : {place.get('phone')}")
    print(f"  Website  : {place.get('website')}")
    print(f"  Rating   : {place.get('totalScore')}")
    print(f"  Reviews  : {place.get('reviewsCount')}")
    print(f"  Claimed  : {place.get('claimed')}")
    print(f"  City     : {place.get('city')}")
    print(f"  Address  : {place.get('address','')[:60]}")
    print(f"  All keys : {list(place.keys())}")
    print()
