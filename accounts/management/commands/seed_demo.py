"""Create demo users, posts and chats for local testing.

    python manage.py seed_demo          # add demo data (idempotent: skips if present)
    python manage.py seed_demo --reset  # delete demo users (and their data) first

All demo accounts use @demo.nits.ac.in emails; log in as any of them from
/dev-login/.
"""
import itertools
import random

from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.models import ConnectionRequest, Profile, User
from accounts.profile_fields import CONNECTIONS, PUBLIC
from chat.models import Message, PriorityRequest
from chat.services import accept_priority_request, connect_users, create_match_conversation
from feed.models import Comment, CommentVote, Post, PostVote

DOMAIN = "demo.nits.ac.in"

PEOPLE = [
    # name, gender, age, likes, year, branch, looking_for, personality, randomize, visible
    ("Ananya Sharma", "female", 20, "male", "2", "Computer Science & Engineering", "relationship", "ambivert", False, True),
    ("Rohan Das", "male", 21, "female", "3", "Electronics & Communication Engineering", "relationship", "extrovert", False, True),
    ("Priya Baruah", "female", 19, "male", "1", "Civil Engineering", "dating", "introvert", True, True),
    ("Arjun Singh", "male", 20, "female", "2", "Mechanical Engineering", "dating", "extrovert", True, True),
    ("Sneha Paul", "female", 22, "male", "4", "Electrical Engineering", "friendship", "ambivert", False, False),
    ("Kabir Ahmed", "male", 22, "female", "4", "Computer Science & Engineering", "relationship", "introvert", True, True),
    ("Ishita Roy", "female", 21, "female", "3", "Electronics & Instrumentation Engineering", "relationship", "introvert", True, True),
    ("Meera Nath", "female", 20, "female,male", "2", "MCA", "relationship", "ambivert", False, True),
    ("Rahul Verma", "male", 23, "female", "pg", "M.Tech", "unsure", "extrovert", False, True),
    ("Tanvi Deka", "female", 19, "male", "1", "Computer Science & Engineering", "study", "ambivert", False, True),
    ("Aditya Kumar", "male", 20, "female", "2", "Electrical Engineering", "dating", "ambivert", True, False),
    ("Sam Lalremruata", "other", 21, "male,female,other", "3", "Civil Engineering", "friendship", "extrovert", False, True),
    ("Nikhil Bora", "male", 24, "male", "phd", "Physics", "relationship", "introvert", True, True),
    ("Riya Sen", "female", 21, "male", "3", "Mechanical Engineering", "dating", "extrovert", False, True),
    # under-18 first-years: matched and connected only with each other
    ("Pooja Kalita", "female", 17, "male", "1", "Electrical Engineering", "friendship", "ambivert", False, True),
    ("Dev Malakar", "male", 17, "female", "1", "Computer Science & Engineering", "friendship", "extrovert", True, True),
    ("Nisha Dutta", "female", 16, "male", "1", "Civil Engineering", "study", "introvert", False, True),
]

INTERESTS = ["music", "football", "anime", "trekking", "photography", "coding", "dance", "cricket", "movies",
             "books", "gaming", "cooking", "badminton", "poetry", "guitar", "sketching", "chess", "travel"]
SKILLS = ["python", "guitar", "public speaking", "video editing", "c++", "singing", "design", "web dev",
          "robotics", "debate", "painting", "ML"]
TOWNS = ["Guwahati", "Silchar", "Kolkata", "Shillong", "Patna", "Agartala", "Imphal", "Jorhat", "Dibrugarh", "Delhi"]
BIOS = [
    "Chai > coffee. Will debate this at the canteen.",
    "Probably at the library or the football ground. No in-between.",
    "Night owl, playlist curator, occasional poet.",
    "Looking for someone to explore Silchar's momo spots with.",
    "Trying to survive end-sems with my sanity intact.",
]
TYPES = ["Kind, funny and doesn't mind my bad puns.", "Someone who loves long walks around the campus lake.",
         "Ambitious but chill.", "Good taste in music, obviously."]

LINES = ["Hey! How's your day going?", "Pretty good! Just got out of a lab 😅", "Which lab?",
         "Digital electronics. The breadboard hates me.", "Haha same energy as my DSA assignments",
         "Do you go to the canteen often?", "Every evening for maggi 🍜", "Okay we have something in common",
         "What music are you into?", "Mostly indie and some old Bollywood", "Arijit or Kishore?", "Kishore. Always.",
         "Respect.", "Have you been to the lake side at sunset?", "Yes! It's the best spot on campus",
         "Any plans for the fest?", "Thinking of joining the dance team", "Oh nice, I'll come cheer 😄",
         "You better!", "Good night, talk tomorrow?", "Sure 😊", "Morning! Did you finish the assignment?",
         "Barely 😭", "Same lol"]

POSTS = [
    ("Best late-night food near campus?", "Canteen closes too early during exams. Where do you all go after 11?"),
    ("Unpopular opinion: 8 AM classes should be illegal", ""),
    ("Lost a black umbrella near the library", "It has a small keychain on it. Please DM if found 🙏"),
    ("Who's going to Incandescence this year?", "Looking for people to go with, first year here!"),
    ("Rate my hostel room setup", "Fairy lights + plants. Thoughts?"),
    ("Study group for Signals & Systems?", "Mid-sems are coming. Let's form a group in the reading room."),
]
COMMENTS = ["Totally agree!", "This is so true 😂", "Hard disagree, but respect.", "Count me in!",
            "Try the dhaba near the gate.", "Following this thread.", "Why is this so relatable",
            "Same here", "I'll DM you", "Underrated post"]


class Command(BaseCommand):
    help = "Create demo users, posts and chats for local testing."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Delete existing demo users first.")

    @transaction.atomic
    def handle(self, *args, **opts):
        demo = User.objects.filter(email__endswith="@" + DOMAIN)
        if opts["reset"]:
            n = demo.count()
            demo.delete()
            self.stdout.write(f"Deleted {n} demo users.")
        elif demo.exists():
            self.stdout.write("Demo data already present (use --reset to recreate).")
            return
        random.seed(42)

        users = {}
        for (name, gender, age, likes, year, branch, looking, pers, randomize, visible) in PEOPLE:
            first, _, last = name.partition(" ")
            username = first.lower() + "." + last.lower().split()[0]
            user = User(username=username, email=f"{username}@{DOMAIN}", first_name=first, last_name=last)
            user.set_unusable_password()
            user.save()
            profile = Profile(
                user=user, name=name, gender=gender, age=age, likes=likes, profile_visible=visible,
                year=year, branch=branch, looking_for=looking, personality=pers,
                bio=random.choice(BIOS), ideal_type=random.choice(TYPES),
                interests=", ".join(random.sample(INTERESTS, 4)), skills=", ".join(random.sample(SKILLS, 3)),
                hometown=random.choice(TOWNS), languages="English, Hindi, " + random.choice(["Bengali", "Assamese", "Mizo"]),
            )
            hidden = CONNECTIONS if randomize else PUBLIC
            profile.init_meta({"name": hidden, "photo": hidden})
            # some variety in edit counters so the (i) buttons have something to show
            for key in random.sample(["age", "bio", "interests", "year", "hometown", "gender"], 2):
                for _ in range(random.randint(1, 4)):
                    profile.record_edit(key)
            profile.save()
            users[first.lower()] = user

        def chat(conv, a, b, count_each):
            lines = itertools.cycle(LINES)
            msgs = []
            for _ in range(count_each):
                msgs.append(Message(conversation=conv, sender=a, stage=conv.tier, text=next(lines)))
                msgs.append(Message(conversation=conv, sender=b, stage=conv.tier, text=next(lines)))
            Message.objects.bulk_create(msgs)

        # 1) Ananya + Rohan: matched, chatted, connected, then 100+ each in Inbox -> "Set priority" shows
        conv = create_match_conversation(users["ananya"], users["rohan"])
        chat(conv, users["ananya"], users["rohan"], 12)
        conv = connect_users(users["ananya"], users["rohan"], via_conversation=conv)
        chat(conv, users["rohan"], users["ananya"], 100)

        # 2) Priya + Arjun: matchmaking chat with 10 each -> "Connect" button shows
        conv = create_match_conversation(users["priya"], users["arjun"])
        chat(conv, users["priya"], users["arjun"], 10)

        # 3) Sneha + Kabir: fresh matchmaking chat, 3 each
        conv = create_match_conversation(users["sneha"], users["kabir"])
        chat(conv, users["kabir"], users["sneha"], 3)

        # 4) Ishita + Meera: connected and already Priority
        conv = connect_users(users["ishita"], users["meera"])
        chat(conv, users["ishita"], users["meera"], 6)
        preq = PriorityRequest.objects.create(conversation=conv, from_user=users["ishita"], to_user=users["meera"])
        accept_priority_request(preq, users["meera"])

        # 5) pending profile requests
        ConnectionRequest.objects.create(from_user=users["rahul"], to_user=users["ananya"], source="profile")
        ConnectionRequest.objects.create(from_user=users["riya"], to_user=users["kabir"], source="profile")
        connect_users(users["tanvi"], users["sam"])

        # posts, nested comments, votes
        everyone = list(users.values())
        for title, body in POSTS:
            post = Post.objects.create(author=random.choice(everyone), title=title, body=body)
            top_level = []
            for _ in range(random.randint(2, 4)):
                top_level.append(Comment.objects.create(post=post, author=random.choice(everyone),
                                                        body=random.choice(COMMENTS)))
            replies = []
            for parent in top_level[:2]:
                r = Comment.objects.create(post=post, author=random.choice(everyone), parent=parent,
                                           body=random.choice(COMMENTS))
                replies.append(r)
                Comment.objects.create(post=post, author=random.choice(everyone), parent=r,
                                       body=random.choice(COMMENTS))
            all_comments = list(post.comments.all())
            post.comment_count = len(all_comments)
            for voter in random.sample(everyone, random.randint(3, 10)):
                PostVote.objects.create(user=voter, post=post, value=random.choice([1, 1, 1, -1]))
                for c in random.sample(all_comments, min(2, len(all_comments))):
                    CommentVote.objects.get_or_create(user=voter, comment=c, defaults={"value": random.choice([1, 1, -1])})
            post.score = sum(post.votes.values_list("value", flat=True))
            post.save()
            for c in all_comments:
                c.score = sum(c.votes.values_list("value", flat=True))
                c.save(update_fields=["score"])

        admin = User(username="site.admin", email=f"admin@{DOMAIN}", first_name="Site", last_name="Admin",
                     is_superuser=True, is_staff=True)
        admin.set_unusable_password()
        admin.save()
        admin_profile = Profile(user=admin, name="Site Admin", gender="other", age=25, likes="male,female,other",
                                profile_visible=False)
        admin_profile.init_meta({"name": PUBLIC})
        admin_profile.save()

        self.stdout.write(self.style.SUCCESS(f"Created {len(users)} demo users. Log in at /dev-login/."))
        self.stdout.write(f"  admin@{DOMAIN}: demo admin (moderation at /mod/)")
        self.stdout.write("  ananya/rohan: inbox chat with 100+ messages each (Set priority)")
        self.stdout.write("  priya/arjun: matchmaking chat with 10 each (Connect)")
        self.stdout.write("  ishita/meera: priority chat · rahul -> ananya pending request")
