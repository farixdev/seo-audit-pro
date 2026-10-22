import csv
import os

FIELD_LABELS = {
    "name": "Business Name",
    "category": "Category",
    "rating": "Rating",
    "review_count": "Review Count",
    "hours": "Hours",
    "claim_this_business": "Claim this business",
    "address": "Address",
    "website": "Website",
    "phone": "Phone",
    "maps_link": "Maps Link",
    "latitude": "Latitude",
    "longitude": "Longitude",
    "place_id": "Place ID",
    "email": "Email",
    "facebook": "Facebook",
    "instagram": "Instagram",
    "linkedin": "LinkedIn",
    "twitter": "Twitter/X",
    "youtube": "YouTube",
    "review_1": "Review 1",
    "review_2": "Review 2",
    "review_3": "Review 3",
    "domain": "Search Domain",
    "area": "Search Area",
}


def export_csv(
    results: list,
    domain: str,
    area: str,
    fields: list,
    output_path: str = "",
) -> str:
    if output_path and output_path.lower().endswith(".csv"):
        filepath = output_path
    else:
        output_dir = output_path or "."
        safe_domain = domain.strip().lower().replace(" ", "_")
        safe_area = area.strip().lower().replace(" ", "_")
        filename = f"{safe_domain}_in_{safe_area}.csv"
        filepath = os.path.join(output_dir, filename)

    headers = [FIELD_LABELS[f] for f in fields if f in FIELD_LABELS]

    with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        for row in results:
            writer.writerow({
                FIELD_LABELS[f]: row.get(f, "")
                for f in fields
                if f in FIELD_LABELS
            })

    return filepath
