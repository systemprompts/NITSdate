"""Registry of every profile field.

Each field gets its own privacy setting (only me / connections / public) and
an edit counter shown behind the (i) button. Adding a field here plus a column
on Profile is all that's needed for it to show up on the profile pages.
"""
from dataclasses import dataclass

ONLY_ME = "me"
CONNECTIONS = "connections"
PUBLIC = "public"
PRIVACY_CHOICES = [
    (ONLY_ME, "Only me"),
    (CONNECTIONS, "Connections"),
    (PUBLIC, "Public"),
]
PRIVACY_LABELS = dict(PRIVACY_CHOICES)

GENDER_CHOICES = [("male", "Male"), ("female", "Female"), ("other", "Others")]

YEAR_CHOICES = [
    ("1", "1st year"),
    ("2", "2nd year"),
    ("3", "3rd year"),
    ("4", "4th year"),
    ("5", "5th year (dual degree)"),
    ("pg", "PG (M.Tech / MCA / MBA / M.Sc)"),
    ("phd", "PhD"),
    ("alumni", "Alumni"),
]

LOOKING_FOR_CHOICES = [
    ("relationship", "A relationship"),
    ("dating", "Casual dating"),
    ("friendship", "Friendship"),
    ("study", "Study buddy"),
    ("unsure", "Not sure yet"),
]

PERSONALITY_CHOICES = [
    ("introvert", "Introvert"),
    ("ambivert", "Ambivert"),
    ("extrovert", "Extrovert"),
]

BRANCH_SUGGESTIONS = [
    "Computer Science & Engineering",
    "Electronics & Communication Engineering",
    "Electrical Engineering",
    "Electronics & Instrumentation Engineering",
    "Mechanical Engineering",
    "Civil Engineering",
    "MCA",
    "MBA",
    "M.Sc",
    "Physics",
    "Chemistry",
    "Mathematics",
]


@dataclass(frozen=True)
class ProfileField:
    key: str
    label: str
    kind: str  # text | textarea | choice | multi | int | tags | image
    choices: tuple = ()
    required: bool = False
    max_length: int = 200
    help: str = ""
    placeholder: str = ""
    default_privacy: str = PUBLIC
    section: str = "about"


FIELDS = [
    ProfileField("name", "Name", "text", required=True, max_length=60, section="basics",
                 help="Hidden fields show your randomized name instead."),
    ProfileField("photo", "Profile photo", "image", section="basics"),
    ProfileField("gender", "Gender", "choice", tuple(GENDER_CHOICES), required=True, section="basics"),
    ProfileField("age", "Age", "birth", required=True, section="basics",
                 help="Worked out from your date of birth. Only moderators can correct the date."),
    ProfileField("likes", "I like", "multi", tuple(GENDER_CHOICES), required=True, section="basics",
                 default_privacy=ONLY_ME, help="Used for matchmaking even when hidden."),
    ProfileField("year", "Currently studying", "choice", tuple(YEAR_CHOICES)),
    ProfileField("branch", "Branch / department", "text", max_length=80,
                 placeholder="e.g. Computer Science & Engineering"),
    ProfileField("looking_for", "Looking for", "choice", tuple(LOOKING_FOR_CHOICES)),
    ProfileField("personality", "Personality type", "choice", tuple(PERSONALITY_CHOICES)),
    ProfileField("ideal_type", "My type", "textarea", max_length=500,
                 placeholder="What kind of person are you into?"),
    ProfileField("bio", "About me", "textarea", max_length=1000,
                 placeholder="A few lines about you"),
    ProfileField("interests", "Interests", "tags", max_length=500,
                 placeholder="music, football, anime, trekking", help="Separate with commas."),
    ProfileField("skills", "Skills", "tags", max_length=500,
                 placeholder="guitar, python, sketching", help="Separate with commas."),
    ProfileField("hometown", "Hometown", "text", max_length=80, placeholder="e.g. Guwahati"),
    ProfileField("languages", "Languages", "tags", max_length=200,
                 placeholder="Bengali, Hindi, English", help="Separate with commas."),
]

FIELD_MAP = {f.key: f for f in FIELDS}
