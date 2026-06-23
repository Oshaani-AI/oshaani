"""Template filters for blog app."""
import re
import markdown
from django import template
from django.utils.safestring import mark_safe

register = template.Library()

# URL pattern for auto-linking (matches http/https URLs not inside HTML attributes)
URL_RE = re.compile(
    r'(?<![="\'>])(https?://[^\s<"\')\]]+)',
    re.IGNORECASE
)


def _linkify_urls(text):
    """Convert bare URLs in text to clickable links. Only processes plain text, not HTML."""
    def replace(match):
        raw = match.group(0)
        # Strip trailing punctuation from URL
        stripped = raw.rstrip('.,;:!?)]\'"')
        trail = raw[len(stripped):]
        url = stripped
        display = url if len(url) <= 60 else url[:57] + '...'
        return f'<a href="{url}" class="blog-url-link" target="_blank" rel="noopener noreferrer">{display}</a>{trail}'
    return URL_RE.sub(replace, text)


def _linkify_html(html):
    """Convert bare URLs in HTML to links, skipping content inside code blocks."""
    # Split by pre/code blocks - we only linkify the text between them
    skip_pattern = re.compile(r'(<pre[^>]*>.*?</pre>|<code[^>]*>.*?</code>)', re.DOTALL | re.IGNORECASE)
    parts = skip_pattern.split(html)
    result = []
    for i, part in enumerate(parts):
        if part.startswith('<pre') or part.startswith('<code'):
            result.append(part)  # Keep code blocks as-is
        else:
            # Process text: split by tags, linkify only text nodes
            sub_parts = re.split(r'(<[^>]+>)', part)
            for sp in sub_parts:
                result.append(_linkify_urls(sp) if sp and not sp.startswith('<') else sp)
    return ''.join(result)


@register.filter
def markdown_content(value):
    """
    Convert markdown to HTML with fenced code blocks for Prism.js syntax highlighting.
    Auto-links bare URLs. Supports: ```language\ncode\n```  for code blocks.
    """
    if not value:
        return ""
    try:
        md = markdown.Markdown(
            extensions=[
                "fenced_code",      # ```code``` blocks
                "tables",           # GitHub-style tables
                "nl2br",            # Newline to <br> (like linebreaks)
                "sane_lists",      # Better list handling
            ]
        )
        html = md.convert(value)
        html = _linkify_html(html)
        return mark_safe(html)
    except Exception:
        # Fallback: escape and use linebreaks for safety
        from django.utils.html import escape
        from django.utils.text import linebreaks
        return mark_safe(linebreaks(escape(value)))
