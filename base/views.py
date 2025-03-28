from django.http import HttpResponse
from datetime import datetime

def sitemap(request):
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S+00:00")
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
        <url>
            <loc>https://www.exprivat.com.ua/</loc>
            <lastmod>{now}</lastmod>
            <changefreq>hourly</changefreq>
            <priority>1.0</priority>
        </url>
    </urlset>
    """
    return HttpResponse(xml, content_type="application/xml")