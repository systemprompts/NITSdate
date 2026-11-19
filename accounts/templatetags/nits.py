import zlib

from django import template
from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from accounts.privacy import display_name, name_is_hidden, photo_visible, visible_gender
from accounts.profile_fields import GENDER_CHOICES

register = template.Library()

# Feather-style 24x24 stroke icons
ICONS = {
    "home": '<path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/>',
    "heart": '<path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/>',
    "chat": '<path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/>',
    "users": '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
    "user": '<path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
    "user-plus": '<path d="M16 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="8.5" cy="7" r="4"/><line x1="20" y1="8" x2="20" y2="14"/><line x1="23" y1="11" x2="17" y2="11"/>',
    "lock": '<rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
    "unlock": '<rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 9.9-1"/>',
    "info": '<circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/>',
    "edit": '<path d="M17 3a2.828 2.828 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z"/>',
    "eye": '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>',
    "globe": '<circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>',
    "star": '<polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>',
    "image": '<rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/>',
    "send": '<line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/>',
    "up": '<polyline points="18 15 12 9 6 15"/>',
    "down": '<polyline points="6 9 12 15 18 9"/>',
    "logout": '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/>',
    "plus": '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>',
    "shield": '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>',
    "archive": '<polyline points="21 8 21 21 3 21 3 8"/><rect x="1" y="3" width="22" height="5"/><line x1="10" y1="12" x2="14" y2="12"/>',
    "zap": '<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>',
    "x": '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
    "check": '<polyline points="20 6 9 17 4 12"/>',
    "comment": '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
    "trash": '<polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>',
    "back": '<line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/>',
    "clock": '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
    "inbox": '<polyline points="22 12 16 12 14 15 10 15 8 12 2 12"/><path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/>',
    "sun": '<circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/>',
    "moon": '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>',
    "slash": '<circle cx="12" cy="12" r="10"/><line x1="4.93" y1="4.93" x2="19.07" y2="19.07"/>',
    "more": '<circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/><circle cx="5" cy="12" r="1"/>',
    "camera": '<path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/>',
    "flag": '<path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z"/><line x1="4" y1="22" x2="4" y2="15"/>',
    "search": '<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>',
    "reply": '<polyline points="9 17 4 12 9 7"/><path d="M20 18v-2a4 4 0 0 0-4-4H4"/>',
}

PRIVACY_ICONS = {"me": "lock", "connections": "users", "public": "globe"}


@register.simple_tag
def icon(name, size=18, cls=""):
    return mark_safe(
        f'<svg class="icon {cls}" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" '
        f'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" '
        f'aria-hidden="true">{ICONS[name]}</svg>'
    )


@register.simple_tag
def privacy_icon(privacy, size=16):
    return icon(PRIVACY_ICONS.get(privacy, "lock"), size)


@register.filter
def name_for(user, viewer):
    return display_name(user, viewer)


HIDDEN_MARK = '<span class="name-hidden" title="This is a randomized name. Their real name is private.">(name hidden)</span>'


GENDER_EMOJI = {"male": "\u2642\ufe0f", "female": "\u2640\ufe0f", "other": "\u26a7\ufe0f"}  # ♂️ ♀️ ⚧️
GENDER_LABELS = dict(GENDER_CHOICES)


@register.simple_tag
def gender_mark(gender):
    """Gender emoji. Pass None when the viewer isn't allowed to see the gender."""
    if gender not in GENDER_EMOJI:
        return ""
    label = GENDER_LABELS[gender]
    return format_html('<span class="gender" role="img" aria-label="{}" title="{}">{}</span>',
                       label, label, GENDER_EMOJI[gender])


@register.filter
def name_html(user, viewer):
    """Display name + gender emoji (if visible to viewer) + "(name hidden)" when it's the alias."""
    html = format_html("{}", display_name(user, viewer))
    gender = gender_mark(visible_gender(user, viewer))
    if gender:
        html = format_html("{}&nbsp;{}", html, gender)
    if viewer is not None and user.pk != viewer.pk and name_is_hidden(user, viewer):
        html = format_html("{} {}", html, mark_safe(HIDDEN_MARK))
    return html


@register.simple_tag
def hidden_mark(hidden):
    return mark_safe(HIDDEN_MARK) if hidden else ""


def _hue(seed):
    """Avatar hues stay in the violet → rose → coral range to match the palette."""
    return 285 + zlib.crc32(str(seed).encode()) % 95


@register.filter
def version(field_file):
    """Cache-busting value that changes whenever the file changes: ?v={{ profile.photo|version }}"""
    return zlib.crc32(field_file.name.encode()) if field_file else 0


@register.simple_tag
def hue(seed):
    return _hue(seed)


@register.simple_tag
def avatar(user, viewer=None, size="md", blind_name=None):
    """Round avatar: the profile photo when the viewer may see it, otherwise a
    coloured initial. `blind_name` forces the anonymous version (matchmaking)."""
    if blind_name is None and photo_visible(user, viewer):
        url = reverse("profile_photo", args=[user.pk])
        version = zlib.crc32(user.profile.photo.name.encode())
        return format_html('<span class="avatar avatar-{}"><img src="{}?v={}" alt="" loading="lazy"></span>',
                           size, url, version)
    label = blind_name or display_name(user, viewer)
    seed = f"{blind_name}" if blind_name else user.pk
    initial = (label.strip()[:1] or "?").upper()
    return format_html('<span class="avatar avatar-{}" style="--h:{}">{}</span>', size, _hue(seed), initial)


@register.simple_tag(takes_context=True)
def qs(context, **kwargs):
    """Current query string with some keys replaced: {% qs page=2 %}"""
    params = context["request"].GET.copy()
    for key, value in kwargs.items():
        if value in (None, ""):
            params.pop(key, None)
        else:
            params[key] = value
    encoded = params.urlencode()
    return f"?{encoded}" if encoded else "?"


@register.filter
def attr(obj, name):
    """{{ obj|attr:"field" }} for dynamic field names."""
    return getattr(obj, name, None)


@register.filter
def choice_labels(csv, field_key):
    """'relationship,friendship'|choice_labels:'looking_for' -> 'A relationship, Friendship'"""
    from accounts.profile_fields import FIELD_MAP

    labels = dict(FIELD_MAP[field_key].choices)
    return ", ".join(labels.get(v, v) for v in (csv or "").split(",") if v)
