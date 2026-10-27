"""Constrained rich-text format shared with the browser editor."""
import re
from urllib.parse import urlsplit
import nh3

RICH_PREFIX = '<!--portal-rich-->'
TAGS = {'p', 'br', 'strong', 'em', 's', 'u', 'code', 'pre', 'blockquote', 'h1', 'h2', 'h3', 'ul', 'ol', 'li', 'hr', 'a', 'img', 'video', 'audio', 'figure', 'figcaption'}
ATTRS = {'a': {'href', 'title'}, 'img': {'src', 'alt', 'title', 'width'}, 'video': {'src', 'controls', 'preload', 'width'}, 'audio': {'src', 'controls', 'preload'}}

def safe_url(value):
    if re.fullmatch(r'/api/product-media/[1-9][0-9]*', value):
        return True
    if re.fullmatch(r'/api/draft-media/[0-9a-f-]{36}/[1-9][0-9]*', value):
        return True
    try:
        url = urlsplit(value)
        return url.scheme in {'http', 'https'} and bool(url.netloc) and not url.username and not url.password and not any(ord(c) < 33 for c in value) and not url.path.lower().endswith('.svg')
    except ValueError:
        return False

def clean_rich(value):
    if not value.startswith(RICH_PREFIX):
        return value
    def attribute(tag, attr, text):
        if attr in {'src', 'href'}:
            return text if safe_url(text) else None
        if attr == 'controls':
            return ''
        if attr == 'preload':
            return 'metadata'
        return text
    cleaned = nh3.clean(value[len(RICH_PREFIX):], tags=TAGS, attributes=ATTRS, attribute_filter=attribute, url_schemes={'http', 'https'}, link_rel='noopener noreferrer', strip_comments=True)
    def normalize_media(match):
        tag, attrs = match.group(1), match.group(2)
        attrs = re.sub(r'\s+(?:controls|preload)(?:\s*=\s*(?:"[^"]*"|\'[^\']*\'|[^\s>]+))?', '', attrs, flags=re.IGNORECASE)
        return f'<{tag}{attrs} controls preload="metadata">'
    cleaned = re.sub(r'<(video|audio)\b([^>]*)>', normalize_media, cleaned, flags=re.IGNORECASE)
    return RICH_PREFIX + cleaned
