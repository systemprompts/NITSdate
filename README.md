# nitsdate — dating & social site for the NITs (Django)

Students of each NIT sign in with their college Google account (the list is in `accounts/campuses.py`).
Every NIT gets its own Campus feed that other NITs can't see; an All NITs feed and matching across NITs
are built and switch on with `ALL_NITS_LIVE=1` (until then they show "Coming soon").

## Run it locally

```bash
cd nitsdate
source .venv/bin/activate              # or: python3 -m venv .venv && pip install -r requirements.txt
python manage.py migrate
python manage.py seed_demo             # optional: 14 demo users, posts, chats (--reset to recreate)
python manage.py runserver
```

Open http://127.0.0.1:8000. Settings live in `.env` (copy `.env.example`). MySQL database: `nitsdate`.

### Testing with several users
`DEV_LOGIN=1` turns on **/dev-login/**: pick any user or create one (no password).
To be two people at once, use a private window, or open `localhost:8000` in one tab and
`127.0.0.1:8000` in another (they keep separate cookies).

Seeded scenarios:
- **ananya / rohan**: inbox chat with 100+ messages each, so **Set priority** is available.
- **priya / arjun**: matchmaking chat with 10 messages each, so **Connect** is available.
- **ishita / meera**: already Priority. **rahul → ananya**: pending profile request.

For quick manual testing you can lower the thresholds in `.env`:
`MATCH_CONNECT_MESSAGES=2`, `PRIORITY_MESSAGES=3`.

### Tests
`python manage.py test accounts`: 15 end-to-end tests (privacy, edit counter, 10/day limit,
matching + 3-day cooldown + expiry, connect at 10+10, priority at 100+100, photo access, votes, comments).

## Google sign-in
1. Google Cloud Console → APIs & Services → Credentials → *OAuth client ID* (Web application).
2. Authorized redirect URI: `http://localhost:8000/accounts/google/login/callback/`
   (plus `https://<your-domain>/accounts/google/login/callback/` in production).
3. Put `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` in `.env`.
4. Who may sign in: `OPEN_CAMPUSES=all` (the default) lets in students of every NIT in
   `accounts/campuses.py`, including departmental addresses such as `…@cse.nits.ac.in`. List codes to open only
   some (`OPEN_CAMPUSES=silchar,warangal`), or `any` to allow every Google account while testing.

## How the rules are implemented
| Rule | Where |
|---|---|
| Per-field privacy (only me / connections / public), edit counter behind (i) | `accounts/profile_fields.py`, `accounts/privacy.py`, `Profile.field_meta` |
| Locked profile (hides public fields from non-connections; name and (i) stay) | `privacy.field_visible` |
| Name shown per viewer (real name vs randomized alias) | `privacy.display_name`, `{{ user\|name_for:viewer }}` |
| 10 profile requests/day (resets at midnight IST), unlimited from matchmaking | `accounts/views.send_request` |
| Matchmaking: mutual "I like" + age + looking-for/personality/year filters, 24 h expiry, 3-day pair cooldown | `matchmaking/services.py` |
| Match chat → Connect at 10+10 → Inbox (match chat archived) → Priority at 100+100 (inbox archived) | `chat/services.py` |
| Blind nicknames inside matchmaking chats; profile link only after connecting | `Conversation.nickname_for`, `chat/room.html` |
| Photos (profile, posts, chat): re-encoded, EXIF/GPS stripped, served only to allowed users | `accounts/images.py` |

## Admins, moderators and reports
- **Admins** are Django superusers (plus any emails in `ADMIN_EMAILS`). Make an account that signed in with
  Google an admin with `python manage.py make_admin their@email` (`--remove` to undo). A demo admin,
  `admin@demo.nits.ac.in`, is available from /dev-login/.
- Admins pick **moderators** by email at **/mod/admin/**; it works even before that person signs up.
- Anyone can **report** posts, comments/replies, messages and profiles (and a whole chat, from the chat's ⊘ menu).
  The reported person never learns who reported them.
- Moderators get a **Mod** tab: the report queue (message reports show the last 10/20/100 messages up to the
  reported one), user search, and a full view of any user (email, every answer and photo regardless of privacy,
  posts, comments, reports). They can remove content and set limits: connection requests per day (0 or more),
  no matchmaking, no posting, no voting/commenting, no messaging, and hide the profile from everyone.
  Every moderator action is logged.

## Deploying on a Linux server (nginx + gunicorn)
Tested layout: Ubuntu, MariaDB 10.5+ (or MySQL 8+), nginx, Python 3.12.

1. `git clone` into `/home/ubuntu/nitsdate`, create `.venv` with Python 3.12, `pip install -r requirements.txt`.
2. Create a database and a dedicated user (never root), then `cp .env.example .env` and fill it in
   (`DEBUG=0`, a long `SECRET_KEY`, your domain, DB credentials, Google keys, `DEV_LOGIN=0`).
3. `python manage.py migrate && python manage.py collectstatic --noinput`
4. `sudo cp deploy/nitsdate.service /etc/systemd/system/ && sudo systemctl enable --now nitsdate`
5. Copy `deploy/nginx-nitsdate.conf` to nginx, set the domain, then `sudo certbot --nginx -d <domain>`.
   To keep the server's IP private behind Cloudflare instead, follow the steps at the top of
   `deploy/nginx-nitsdate-cloudflare.conf` (Origin CA certificate, `deploy/cloudflare-ips.sh`).
6. After your first Google sign-in: `python manage.py make_admin <your email>`.
7. Nightly backups: add `deploy/backup.sh` to cron (see the comment at the top of the file).
8. Live updates (optional but recommended): install Redis (`sudo apt install redis-server`), add
   `REDIS_URL=redis://127.0.0.1:6379/3` to `.env`, then
   `sudo cp deploy/nitsdate-live.service /etc/systemd/system/ && sudo systemctl enable --now nitsdate-live`.
   The nginx files already send `/ws/` to it. It pushes badge counts, new chat messages and matches to
   open pages over a WebSocket and runs the background matchmaker. Without it, pages ask on a timer
   instead, and matches happen while someone has the match page open.

`python manage.py check --deploy` should only warn about HSTS subdomains/preload, which are off on purpose
until the final domain is settled.

### Photos
`MEDIA_STORAGE=local` keeps photos on the server. `MEDIA_STORAGE=s3` stores them in any private
S3-compatible bucket (Cloudflare R2, Oracle Object Storage, ...); photos are always served through
permission-checking views, never from public bucket URLs.

### Privacy defaults
Search engines are told not to index anything (`robots.txt`, `X-Robots-Tag`, meta tags), the Django admin
lives at a custom `ADMIN_URL`, password/email account pages are disabled (sign-in is Google-only), and
uploaded photos have their EXIF/GPS data stripped. `/privacy/` and `/terms/` describe all of this for members;
set `CONTACT_EMAIL` so they know where to send deletion requests.
