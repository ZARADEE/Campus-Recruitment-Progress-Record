"""Read-only helpers for inspecting public careers pages and their public assets."""
import argparse
import json
import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin
from refresh_opportunities import fetch, OUT

parser = argparse.ArgumentParser()
parser.add_argument('url')
parser.add_argument('pattern', nargs='?', default=r'channel_online_status|storefront_list')
parser.add_argument('--assets', action='store_true')
parser.add_argument('--case-sensitive', action='store_true')
args = parser.parse_args()
html = fetch(args.url)
urls = [args.url]
if args.assets:
    sources = [next(group for group in match if group) for match in re.findall(r'<script[^>]+src=(?:"([^"]+)"|\'([^\']+)\'|([^\s>]+))', html)]
    urls = list(dict.fromkeys(urljoin(args.url, value) for value in sources))
    print('ASSETS', json.dumps(urls, ensure_ascii=False))

def inspect(url):
    try:
        content = fetch(url)
        matches = list(re.finditer(args.pattern, content, 0 if args.case_sensitive else re.I))
        return {'url': url, 'length': len(content), 'matches': [content[max(0, m.start()-230):m.end()+400] for m in matches[:18]]}
    except Exception as error:
        return {'url': url, 'error': str(error)}

with ThreadPoolExecutor(max_workers=6) as pool:
    for item in pool.map(inspect, urls):
        print(json.dumps(item, ensure_ascii=False))
