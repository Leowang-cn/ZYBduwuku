"""Constrained rich-text format shared with the browser editor."""
import re
from html.parser import HTMLParser
from urllib.parse import urlsplit
import nh3

RICH_PREFIX = '<!--portal-rich-->'
TAGS = {'p', 'br', 'strong', 'em', 's', 'u', 'code', 'pre', 'blockquote', 'h1', 'h2', 'h3', 'ul', 'ol', 'li', 'hr', 'a', 'img', 'video', 'audio', 'figure', 'figcaption', 'table', 'thead', 'tbody', 'tfoot', 'tr', 'th', 'td'}
ATTRS = {'a': {'href', 'title'}, 'p': {'style'}, 'h1': {'style'}, 'h2': {'style'}, 'h3': {'style'}, 'figure': {'style'}, 'img': {'src', 'alt', 'title', 'width', 'style'}, 'video': {'src', 'controls', 'preload', 'width', 'style'}, 'audio': {'src', 'controls', 'preload'}, 'td': {'colspan', 'rowspan', 'colwidth'}, 'th': {'colspan', 'rowspan', 'colwidth'}}

def safe_url(value):
    if re.fullmatch(r'/api/(?:product-media|solution-media)/[1-9][0-9]*', value):
        return True
    if re.fullmatch(r'/api/(?:draft-media|solution-draft-media)/[0-9a-f-]{36}/[1-9][0-9]*', value):
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
        if attr == 'style' and tag in {'p', 'h1', 'h2', 'h3', 'figure', 'img', 'video'}:
            declarations = [item.strip() for item in text.split(';') if item.strip()]
            safe_styles = []
            for declaration in declarations:
                if re.fullmatch(r'text-align:\s*(?:left|center|right|justify)', declaration, re.IGNORECASE):
                    safe_styles.append(declaration)
                else:
                    match = re.fullmatch(r'width:\s*(\d{1,4})px', declaration, re.IGNORECASE)
                    if tag in {'img', 'video'} and match and 1 <= int(match.group(1)) <= 2000:
                        safe_styles.append(declaration)
            return '; '.join(safe_styles) if safe_styles else None
        if attr == 'width' and tag in {'img', 'video', 'td', 'th'}:
            return text if re.fullmatch(r'\d{1,4}', text) and 1 <= int(text) <= 2000 else None
        if attr in {'colspan', 'rowspan'} and tag in {'td', 'th'}:
            return text if re.fullmatch(r'[1-9]\d?', text) and int(text) <= 20 else None
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


def meaningful_rich(value):
    """Count visible rich text, not markup; leave legacy Markdown untouched."""
    if not value.startswith(RICH_PREFIX):
        return len(value.strip()) >= 10
    class Content(HTMLParser):
        def __init__(self):
            super().__init__()
            self.text = []
            self.image = False

        def handle_data(self, data):
            self.text.append(data)

        def handle_starttag(self, tag, attrs):
            if tag == 'img' and dict(attrs).get('src'):
                self.image = True
    content = Content()
    content.feed(value[len(RICH_PREFIX):])
    text = ''.join(content.text)
    return len(''.join(c for c in text if not c.isspace() and c not in '\u200b\u200c\u200d\ufeff')) >= 10 or content.image
