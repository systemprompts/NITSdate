"""Pairs open matchmaking searches in the background, so a match happens within a couple of seconds
even when nobody is watching the match page. Runs inside the WebSocket service (config/asgi.py).

The pages still call matchmaking.services.try_match themselves when the socket is down; doing both
is safe because try_match locks the two searches before pairing them."""
import asyncio
import logging
import time
from datetime import timedelta

from channels.db import database_sync_to_async
from django.conf import settings
from django.utils import timezone

log = logging.getLogger(__name__)

TICK = 2            # seconds between rounds
SWEEP_EVERY = 60    # every open search is tried again this often, in case a round missed a pair
PER_ROUND = 500


class Matcher:
    def __init__(self):
        self.tried_until = None   # searches that became matchable before this were already tried
        self.swept_at = 0.0

    def run_once(self):
        """One round. A new pair can only appear when a search becomes matchable (it's
        MATCH_REVEAL_SECONDS old), so normally only those are tried. Returns how many pairs it made."""
        from matchmaking.models import MatchSession
        from matchmaking.services import activity_rank, expire_stale, try_match

        expire_stale()
        settled = timezone.now() - timedelta(seconds=settings.MATCH_REVEAL_SECONDS)
        searches = MatchSession.objects.active().filter(created_at__lte=settled)
        if self.tried_until is None or time.monotonic() - self.swept_at >= SWEEP_EVERY:
            self.swept_at = time.monotonic()
        else:
            searches = searches.filter(created_at__gt=self.tried_until)
        self.tried_until = settled
        paired = set()   # people matched in this round; their other row needn't be tried
        # the same order a search picks its partner in: most recently active first, then longest waiting
        for search in searches.annotate(activity=activity_rank()).order_by("activity", "created_at", "id")[:PER_ROUND]:
            if search.user_id in paired:
                continue
            result = try_match(search)
            if result.status == MatchSession.MATCHED:
                paired.update({result.user_id, result.matched_with_id})
        return len(paired) // 2


async def run_forever():
    matcher = Matcher()
    run = database_sync_to_async(matcher.run_once, thread_sensitive=False)
    while True:
        try:
            await run()
        except Exception:
            log.exception("matchmaking round failed")
        await asyncio.sleep(TICK)
