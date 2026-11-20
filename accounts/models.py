import random

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone

from .profile_fields import FIELD_MAP, FIELDS, GENDER_CHOICES, PRIVACY_CHOICES

ADJECTIVES = [
    "Midnight", "Velvet", "Cosmic", "Silent", "Golden", "Misty", "Lunar", "Crimson", "Electric",
    "Gentle", "Wild", "Hidden", "Sunny", "Rainy", "Dreamy", "Mellow", "Witty", "Brave", "Quiet",
    "Sparkly", "Cozy", "Neon", "Starry", "Breezy", "Curious", "Lucky", "Mystic", "Shy", "Bold",
    "Frosty", "Amber", "Jade", "Scarlet", "Indigo", "Coral", "Hazel", "Sleepy", "Clever",
]
ANIMALS = [
    "Otter", "Panda", "Falcon", "Koala", "Fox", "Dolphin", "Owl", "Tiger", "Rhino", "Hornbill",
    "Penguin", "Lynx", "Sparrow", "Turtle", "Hummingbird", "Wolf", "Deer", "Firefly", "Raven",
    "Squirrel", "Peacock", "Kitten", "Puffin", "Panther", "Seal", "Butterfly", "Gecko", "Swan",
]


def generate_alias():
    for _ in range(50):
        alias = f"{random.choice(ADJECTIVES)} {random.choice(ANIMALS)} {random.randint(10, 99)}"
        if not User.objects.filter(alias=alias).exists():
            return alias
    return f"{random.choice(ADJECTIVES)} {random.choice(ANIMALS)} {random.randint(1000, 9999)}"


class User(AbstractUser):
    alias = models.CharField(max_length=40, unique=True, blank=True,
                             help_text="Randomized name shown when the real name is hidden.")
    # when they were last on the site (updated at most once a minute). Matchmaking uses it to pair
    # people who are around first; moderators see it on the user's mod page. Never shown to students.
    last_seen = models.DateTimeField(null=True, blank=True, editable=False)
    # which notifications they want (set on the notifications page)
    notify_upvotes = models.BooleanField(default=True)
    notify_comments = models.BooleanField(default=True)
    notify_replies = models.BooleanField(default=True)

    def save(self, *args, **kwargs):
        if not self.alias:
            self.alias = generate_alias()
        super().save(*args, **kwargs)

    @property
    def has_profile(self):
        return hasattr(self, "profile")


class Profile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")

    # basics (asked at sign-up)
    name = models.CharField(max_length=60)
    gender = models.CharField(max_length=10, choices=GENDER_CHOICES)
    birth_date = models.DateField(help_text="Exact date of birth; age is always calculated from it.")
    likes = models.CharField(max_length=40, help_text="Comma separated genders")
    profile_visible = models.BooleanField(
        default=True, help_text="False = locked profile: only connections see your details.")

    # extra info, filled in any time from the profile page
    photo = models.ImageField(upload_to="profile/%Y/%m/", blank=True)
    year = models.CharField(max_length=10, blank=True)
    branch = models.CharField(max_length=80, blank=True)
    looking_for = models.CharField(max_length=20, blank=True)
    personality = models.CharField(max_length=20, blank=True)
    ideal_type = models.TextField(blank=True)
    bio = models.TextField(blank=True)
    interests = models.TextField(blank=True)
    skills = models.TextField(blank=True)
    hometown = models.CharField(max_length=80, blank=True)
    languages = models.CharField(max_length=200, blank=True)

    # {"<field>": {"privacy": "public", "edits": 0, "last_edited": "<iso>"}}
    field_meta = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} ({self.user.alias})"

    # ---- per-field meta --------------------------------------------------
    def _meta_for(self, key):
        meta = self.field_meta.get(key)
        if meta is None:
            meta = {"privacy": FIELD_MAP[key].default_privacy, "edits": 0, "last_edited": None}
            self.field_meta[key] = meta
        return meta

    def privacy_of(self, key):
        return self._meta_for(key)["privacy"]

    def edits_of(self, key):
        return self._meta_for(key)["edits"]

    def last_edited_of(self, key):
        value = self._meta_for(key).get("last_edited")
        return timezone.datetime.fromisoformat(value) if value else None

    def set_privacy(self, key, privacy):
        """Privacy changes never count as an edit."""
        assert privacy in dict(PRIVACY_CHOICES)
        self._meta_for(key)["privacy"] = privacy

    def record_edit(self, key):
        meta = self._meta_for(key)
        meta["edits"] = meta.get("edits", 0) + 1
        meta["last_edited"] = timezone.now().isoformat()

    def init_meta(self, overrides=None):
        overrides = overrides or {}
        for f in FIELDS:
            self.field_meta[f.key] = {
                "privacy": overrides.get(f.key, f.default_privacy), "edits": 0, "last_edited": None,
            }

    # ---- age ---------------------------------------------------------------
    @property
    def age(self):
        if self.birth_date is None:
            return None
        today = timezone.localdate()
        b = self.birth_date
        return today.year - b.year - ((today.month, today.day) < (b.month, b.day))

    @age.setter
    def age(self, years):
        """Convenience for tests/seeding: a birth date that gives `years` today."""
        today = timezone.localdate() - timezone.timedelta(days=30)
        try:
            self.birth_date = today.replace(year=today.year - years)
        except ValueError:  # 29 February
            self.birth_date = today.replace(year=today.year - years, day=28)

    @property
    def is_minor(self):
        return self.age is not None and self.age < settings.ADULT_AGE

    @property
    def adult_on(self):
        """The date this person turns 18."""
        b = self.birth_date
        try:
            return b.replace(year=b.year + settings.ADULT_AGE)
        except ValueError:  # born 29 February: `age` counts them as 18 from 1 March in common years
            return b.replace(year=b.year + settings.ADULT_AGE, month=3, day=1)

    # ---- values ----------------------------------------------------------
    @property
    def likes_list(self):
        return [g for g in self.likes.split(",") if g]

    def raw_value(self, key):
        value = getattr(self, key)
        if FIELD_MAP[key].kind in ("multi", "tags"):
            return [v.strip() for v in (value or "").split(",") if v.strip()]
        return value

    def display_value(self, key):
        """Human readable value: str, list of str (tags/multi) or '' when empty."""
        field = FIELD_MAP[key]
        value = self.raw_value(key)
        if field.kind == "choice":
            return dict(field.choices).get(value, "")
        if field.kind == "multi":
            labels = dict(field.choices)
            return [labels.get(v, v) for v in value]
        if field.kind == "image":
            return bool(value)
        if field.kind in ("int", "birth"):
            return "" if value is None else str(value)
        return value or ""


def pair_ids(a, b):
    a_id = getattr(a, "pk", a)
    b_id = getattr(b, "pk", b)
    return (a_id, b_id) if a_id < b_id else (b_id, a_id)


class Connection(models.Model):
    """An accepted connection. Stored once per pair with user1.id < user2.id."""
    user1 = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    user2 = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user1", "user2"], name="uniq_connection_pair")]

    def other(self, user):
        return self.user2 if user.pk == self.user1_id else self.user1


class ConnectionRequest(models.Model):
    SOURCE_PROFILE = "profile"
    SOURCE_MATCH = "match"
    SOURCES = [(SOURCE_PROFILE, "From profile"), (SOURCE_MATCH, "From matchmaking")]

    PENDING, ACCEPTED, DECLINED, CANCELLED = "pending", "accepted", "declined", "cancelled"
    STATUSES = [(PENDING, "Pending"), (ACCEPTED, "Accepted"), (DECLINED, "Declined"), (CANCELLED, "Cancelled")]

    from_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="sent_requests")
    to_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="received_requests")
    source = models.CharField(max_length=10, choices=SOURCES)
    conversation = models.ForeignKey("chat.Conversation", null=True, blank=True, on_delete=models.CASCADE,
                                     related_name="connection_requests")
    status = models.CharField(max_length=10, choices=STATUSES, default=PENDING)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["from_user", "source", "created_at"])]


class Block(models.Model):
    """One person's blocks against another. Two independent kinds:
    messages – chats between them are paused (neither side can send);
    profile  – the blocked person can't see the blocker's profile, photos or real name.
    The row is deleted when both are switched off."""
    blocker = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="blocks_made")
    blocked = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="blocks_received")
    messages = models.BooleanField(default=False)
    profile = models.BooleanField(default=False)
    conversation = models.ForeignKey("chat.Conversation", null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name="+", help_text="Chat it was created from (keeps blind names blind).")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["blocker", "blocked"], name="uniq_block_pair")]


class GalleryPhoto(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="gallery")
    image = models.ImageField(upload_to="gallery/%Y/%m/")
    privacy = models.CharField(max_length=12, choices=PRIVACY_CHOICES, default="public")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
