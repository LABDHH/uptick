"""YouTube Data API v3 client.

Two rules govern this module (§1):

Rule 1 — never use search.list to fetch a channel's videos. search.list costs
100 units AND one of only 100 daily search calls, *per channel*. The
uploads-playlist path costs 1 unit and 0 search calls. See recent_uploads().

Rule 2 — find sponsorship-proven creators with search's
videoPaidProductPlacement filter, not a model. High precision, low recall:
reward its presence, never penalize its absence.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import random
import re
from typing import Any, Iterable, Optional

import httpx

import cache

BASE = "https://www.googleapis.com/youtube/v3"
TIMEOUT = httpx.Timeout(20.0, connect=10.0)


class QuotaExceeded(Exception):
    """403 quotaExceeded — daily units gone. Stop until midnight Pacific."""


class SearchCapReached(Exception):
    """Our own 100/day search-call counter, blocked at 90."""


class YouTubeError(Exception):
    pass


def _hash(*parts: Any) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:32]


def _chunks(items: list, n: int) -> Iterable[list]:
    for i in range(0, len(items), n):
        yield items[i : i + n]


_ISO_DUR = re.compile(
    r"P(?:(?P<d>\d+)D)?T(?:(?P<h>\d+)H)?(?:(?P<m>\d+)M)?(?:(?P<s>\d+)S)?"
)


def parse_duration(iso: str | None) -> int:
    """ISO-8601 duration → seconds. Returns 0 when absent or unparseable."""
    if not iso:
        return 0
    m = _ISO_DUR.match(iso)
    if not m:
        return 0
    d = int(m.group("d") or 0)
    h = int(m.group("h") or 0)
    mi = int(m.group("m") or 0)
    s = int(m.group("s") or 0)
    return d * 86400 + h * 3600 + mi * 60 + s


# channels.list costs 1 unit per call whatever parts are asked for, so the
# topic categories and the creator's own keywords come free. Both feed the
# relevance pre-screen.
CHANNEL_PARTS = "snippet,statistics,contentDetails,status,topicDetails,brandingSettings"
CHANNEL_FIELDS = (
    "items(id,snippet(title,description,country,publishedAt,customUrl),"
    "statistics(subscriberCount,videoCount,viewCount,hiddenSubscriberCount),"
    "contentDetails/relatedPlaylists/uploads,status/madeForKids,"
    "topicDetails/topicCategories,brandingSettings/channel/keywords)"
)

# videos.list: tags and audio language describe the audience; the paid
# product placement flag is the creator's own disclosure on EVERY upload, not
# only on videos a paid-placement search happened to return.
VIDEO_PARTS_FULL = "snippet,statistics,contentDetails,paidProductPlacementDetails"
VIDEO_PARTS_BASE = "snippet,statistics,contentDetails"
VIDEO_FIELDS_FULL = (
    "items(id,snippet(channelId,title,description,publishedAt,"
    "liveBroadcastContent,tags,defaultAudioLanguage),"
    "statistics(viewCount,likeCount,commentCount),contentDetails/duration,"
    "paidProductPlacementDetails/hasPaidProductPlacement)"
)
VIDEO_FIELDS_BASE = (
    "items(id,snippet(channelId,title,description,publishedAt,"
    "liveBroadcastContent,tags,defaultAudioLanguage),"
    "statistics(viewCount,likeCount,commentCount),contentDetails/duration)"
)

# Creator references as a model writes them. Parsed in code, then confirmed
# against the API: a reference that does not resolve to a real channel was
# invented, and is dropped.
_RE_CHANNEL_ID = re.compile(r"\b(UC[\w-]{22})\b")
_RE_HANDLE = re.compile(r"(?:youtube\.com/)?@([\w.\-]{3,30})")
_RE_VIDEO = re.compile(
    r"(?:youtube\.com/(?:watch\?(?:.*&)?v=|shorts/|live/)|youtu\.be/)([\w-]{11})"
)


def parse_ref(ref: str) -> tuple[str, str] | None:
    """('channel'|'handle'|'video', value), or None for a bare name."""
    if not ref:
        return None
    ref = ref.strip()
    m = _RE_CHANNEL_ID.search(ref)
    if m:
        return "channel", m.group(1)
    m = _RE_VIDEO.search(ref)
    if m:
        return "video", m.group(1)
    m = _RE_HANDLE.search(ref)
    if m and (ref.startswith("@") or "youtube.com/@" in ref):
        return "handle", m.group(1)
    return None


class YouTubeClient:
    # Flipped off for the life of the process if the API ever rejects the
    # paid-placement part, so enrichment keeps working without it.
    _paid_part_ok = True

    def __init__(self, api_key: str):
        self.key = api_key
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self):
        self._client = httpx.AsyncClient(timeout=TIMEOUT)
        return self

    async def __aexit__(self, *exc):
        if self._client:
            await self._client.aclose()

    # ------------------------------------------------------------ transport

    async def _get(self, path: str, params: dict, units: int, search_call: bool = False) -> dict:
        """One request, with the §2 error taxonomy.

        quotaExceeded and rateLimitExceeded are both 403 but demand opposite
        responses: stop forever vs. back off and retry.
        """
        if not cache.can_spend(search_calls=1 if search_call else 0, units=units):
            if search_call:
                raise SearchCapReached(
                    "Daily search-call budget reached (blocked at 90/100). "
                    "Serving cached results only until midnight Pacific."
                )
            raise QuotaExceeded("Daily unit budget reached.")

        params = {k: v for k, v in params.items() if v is not None}
        params["key"] = self.key

        attempt = 0
        while True:
            try:
                resp = await self._client.get(f"{BASE}/{path}", params=params)
            except httpx.RequestError as e:
                if attempt >= 3:
                    raise YouTubeError(f"network error calling {path}: {e}") from e
                await asyncio.sleep(min(2**attempt, 8) + random.random())
                attempt += 1
                continue

            if resp.status_code == 200:
                cache.record_spend(search_calls=1 if search_call else 0, units=units)
                return resp.json()

            if resp.status_code == 403:
                reason = _reason(resp)
                if reason in ("quotaExceeded", "dailyLimitExceeded"):
                    # Do NOT retry. Units are gone until midnight Pacific.
                    cache.mark_quota_exhausted()
                    raise QuotaExceeded(
                        "YouTube daily quota exhausted. Cached results only "
                        "until midnight Pacific."
                    )
                if reason in ("rateLimitExceeded", "userRateLimitExceeded"):
                    # Opposite of the above: back off and retry.
                    if attempt >= 4:
                        raise YouTubeError("rateLimitExceeded after retries")
                    await asyncio.sleep(min(2**attempt, 8) + random.random())
                    attempt += 1
                    continue
                raise YouTubeError(f"403 {reason} on {path}")

            if resp.status_code == 404:
                return {"items": []}

            if resp.status_code >= 500:
                if attempt >= 3:
                    raise YouTubeError(f"{resp.status_code} on {path}")
                await asyncio.sleep(min(2**attempt, 8) + random.random())
                attempt += 1
                continue

            raise YouTubeError(f"{resp.status_code} on {path}: {resp.text[:200]}")

    # ------------------------------------------------------------ 1. categories

    async def video_categories(self, region_code: str) -> list[dict]:
        """Real category IDs, fed to Agent 2 so it cannot invent one."""
        h = _hash("categories", region_code)
        hit = cache.get_search(h)
        if hit is not None:
            return hit
        data = await self._get(
            "videoCategories",
            {"part": "snippet", "regionCode": region_code},
            units=1,
        )
        cats = [
            {"id": it["id"], "title": it["snippet"]["title"]}
            for it in data.get("items", [])
            if it.get("snippet", {}).get("assignable", True)
        ]
        cache.put_search(h, cats)
        return cats

    # ------------------------------------------------------------ 2/3. search

    async def search_videos(
        self,
        q: str,
        region_code: str,
        category_id: Optional[str] = None,
        relevance_language: Optional[str] = None,
        paid_placement_only: bool = False,
        max_results: int = 50,
        published_after: Optional[str] = None,
    ) -> list[dict]:
        """Rule 2 lives here.

        paid_placement_only=True returns only videos the creator flagged as a
        paid promotion — creator-integrated deals, not YouTube's programmatic
        pre-rolls. High precision, low recall.
        """
        h = _hash("search", q, region_code, category_id, relevance_language,
                  paid_placement_only, max_results, published_after)
        hit = cache.get_search(h)
        if hit is not None:
            return hit

        params = {
            "part": "snippet",
            "type": "video",
            "q": q,
            "regionCode": region_code,
            "videoCategoryId": category_id,
            "relevanceLanguage": relevance_language,
            "order": "relevance",
            "maxResults": max_results,
            # Anything older than the dormancy window would be excluded after
            # enrichment anyway, so do not spend a search surfacing it.
            "publishedAfter": published_after,
            "fields": "items(id/videoId,snippet(channelId,channelTitle,title))",
        }
        if paid_placement_only:
            params["videoPaidProductPlacement"] = "true"

        data = await self._get("search", params, units=100, search_call=True)
        items = [
            {
                "video_id": it["id"]["videoId"],
                "channel_id": it["snippet"]["channelId"],
                "channel_title": it["snippet"].get("channelTitle", ""),
                "title": it["snippet"].get("title", ""),
                "paid_placement_hit": paid_placement_only,
            }
            for it in data.get("items", [])
            if it.get("id", {}).get("videoId")
        ]
        cache.put_search(h, items)
        return items

    # ------------------------------------------------------------ 4. trending

    async def trending_channels(
        self, region_code: str, category_id: Optional[str] = None
    ) -> set[str]:
        """Cheap (1 unit) and no search call — hence the small +5 bonus."""
        h = _hash("trending", region_code, category_id)
        hit = cache.get_search(h)
        if hit is not None:
            return set(hit)
        try:
            data = await self._get(
                "videos",
                {
                    "part": "snippet",
                    "chart": "mostPopular",
                    "regionCode": region_code,
                    "videoCategoryId": category_id,
                    "maxResults": 50,
                    "fields": "items(id,snippet/channelId)",
                },
                units=1,
            )
        except YouTubeError:
            # Some category/region pairs legitimately return no chart.
            return set()
        ids = [
            it["snippet"]["channelId"]
            for it in data.get("items", [])
            if it.get("snippet", {}).get("channelId")
        ]
        cache.put_search(h, ids)
        return set(ids)

    # ------------------------------------------------------------ 5. channels

    async def channels(self, channel_ids: list[str]) -> dict[str, dict]:
        """Batch 50 per call — 1 unit each."""
        channel_ids = list(dict.fromkeys(channel_ids))
        out = cache.get_channels(channel_ids)
        missing = [c for c in channel_ids if c not in out]

        fetched: list[dict] = []
        for batch in _chunks(missing, 50):
            data = await self._get(
                "channels",
                {
                    "part": CHANNEL_PARTS,
                    "id": ",".join(batch),
                    "maxResults": 50,
                    "fields": CHANNEL_FIELDS,
                },
                units=1,
            )
            for it in data.get("items", []):
                fetched.append(it)
                out[it["id"]] = it
        if fetched:
            cache.put_channels(fetched)
        return out

    # ------------------------------------------------------------ 6. uploads

    async def recent_uploads(self, uploads_playlist: str, max_results: int = 20) -> list[str]:
        """Rule 1: 1 unit, 0 search calls, ~100x cheaper than search.list.

        Read the playlist id from contentDetails.relatedPlaylists.uploads —
        never assume the UC->UU string swap, which is undocumented behavior.
        """
        if not uploads_playlist:
            return []
        h = _hash("uploads", uploads_playlist, max_results)
        hit = cache.get_search(h)
        if hit is not None:
            return hit
        try:
            data = await self._get(
                "playlistItems",
                {
                    "part": "contentDetails",
                    "playlistId": uploads_playlist,
                    "maxResults": max_results,
                    "fields": "items/contentDetails/videoId",
                },
                units=1,
            )
        except YouTubeError:
            return []
        ids = [
            it["contentDetails"]["videoId"]
            for it in data.get("items", [])
            if it.get("contentDetails", {}).get("videoId")
        ]
        cache.put_search(h, ids)
        return ids

    # ------------------------------------------------------------ 7. videos

    async def videos(self, video_ids: list[str]) -> dict[str, dict]:
        """Batch 50 per call.

        Deleted or private videos still appear in playlistItems but are absent
        from videos.list — so callers must reconcile by ID, never by position.
        """
        video_ids = list(dict.fromkeys(video_ids))
        out = cache.get_videos(video_ids)
        missing = [v for v in video_ids if v not in out]

        fetched: list[dict] = []
        for batch in _chunks(missing, 50):
            data = await self._videos_batch(batch)
            for it in data.get("items", []):
                it["_duration_s"] = parse_duration(
                    it.get("contentDetails", {}).get("duration")
                )
                fetched.append(it)
                out[it["id"]] = it
        if fetched:
            cache.put_videos(fetched)
        return out


    async def _videos_batch(self, batch: list[str]) -> dict:
        """One videos.list call, asking for the paid-placement flag when the
        API accepts it. A 400 on that part retries once without it, so a
        change on Google's side can never break enrichment."""
        if YouTubeClient._paid_part_ok:
            try:
                return await self._get(
                    "videos",
                    {"part": VIDEO_PARTS_FULL, "id": ",".join(batch),
                     "maxResults": 50, "fields": VIDEO_FIELDS_FULL},
                    units=1,
                )
            except YouTubeError as e:
                if not str(e).startswith("400"):
                    raise
                YouTubeClient._paid_part_ok = False
        return await self._get(
            "videos",
            {"part": VIDEO_PARTS_BASE, "id": ",".join(batch),
             "maxResults": 50, "fields": VIDEO_FIELDS_BASE},
            units=1,
        )

    # ------------------------------------------------------------ 8. trending pool

    async def trending_pool(
        self, region_code: str, category_id: Optional[str] = None, pages: int = 2,
    ) -> list[dict]:
        """The most-popular chart as a SOURCE of creators, not just a flag.

        1 unit per page of 50 and no search call, so it costs a fraction of one
        keyword search. Returns one row per chart video, in chart order.
        """
        h = _hash("trending_pool", region_code, category_id, pages)
        hit = cache.get_search(h)
        if hit is not None:
            return hit
        out: list[dict] = []
        token = None
        for _ in range(max(1, pages)):
            try:
                data = await self._get(
                    "videos",
                    {
                        "part": "snippet",
                        "chart": "mostPopular",
                        "regionCode": region_code,
                        "videoCategoryId": category_id,
                        "maxResults": 50,
                        "pageToken": token,
                        "fields": "nextPageToken,items(id,snippet(channelId,"
                                  "channelTitle,title,categoryId))",
                    },
                    units=1,
                )
            except YouTubeError:
                # Some category/region pairs legitimately have no chart.
                break
            for it in data.get("items", []):
                sn = it.get("snippet", {})
                if not sn.get("channelId"):
                    continue
                out.append({
                    "video_id": it.get("id"),
                    "channel_id": sn["channelId"],
                    "channel_title": sn.get("channelTitle", ""),
                    "title": sn.get("title", ""),
                    "category_id": category_id or sn.get("categoryId") or "",
                    "rank": len(out) + 1,
                })
            token = data.get("nextPageToken")
            if not token:
                break
        cache.put_search(h, out)
        return out

    # ------------------------------------------------------------ 9. resolve refs

    async def channel_by_handle(self, handle: str) -> Optional[dict]:
        """channels.list?forHandle — 1 unit, no search call."""
        handle = handle.lstrip("@")
        h = _hash("handle", handle.lower())
        hit = cache.get_search(h)
        if hit is not None:
            if not hit:
                return None
            got = await self.channels([hit])
            return got.get(hit)
        try:
            data = await self._get(
                "channels",
                {"part": CHANNEL_PARTS, "forHandle": "@" + handle,
                 "fields": CHANNEL_FIELDS},
                units=1,
            )
        except YouTubeError:
            return None
        items = data.get("items") or []
        if not items:
            cache.put_search(h, "")
            return None
        cache.put_channels(items[:1])
        cache.put_search(h, items[0]["id"])
        return items[0]

    async def resolve_refs(self, refs: list[str]) -> dict[str, str]:
        """{ref: channel_id} for every reference that names a REAL channel.

        Channel URLs and ids are confirmed by the batched channels.list the
        caller runs next; handles cost 1 unit each; video links resolve
        through videos.list in batches of 50. Bare names are not resolved
        here (that would cost a search call); the caller matches them against
        titles it already has.
        """
        out: dict[str, str] = {}
        handles: dict[str, str] = {}
        vids: dict[str, str] = {}
        for ref in dict.fromkeys(r for r in refs if r):
            kind = parse_ref(ref)
            if not kind:
                continue
            if kind[0] == "channel":
                out[ref] = kind[1]
            elif kind[0] == "handle":
                handles[ref] = kind[1]
            else:
                vids[ref] = kind[1]

        if vids:
            try:
                got = await self.videos(list(vids.values()))
            except (YouTubeError, QuotaExceeded):
                got = {}
            for ref, vid in vids.items():
                cid = (got.get(vid) or {}).get("snippet", {}).get("channelId")
                if cid:
                    out[ref] = cid

        sem = asyncio.Semaphore(8)

        async def _one(ref: str, handle: str):
            async with sem:
                ch = await self.channel_by_handle(handle)
                return ref, (ch or {}).get("id")

        for ref, cid in await asyncio.gather(*[_one(r, h) for r, h in handles.items()]):
            if cid:
                out[ref] = cid
        return out

    # ------------------------------------------------------------ 10. comments

    async def top_comments(self, video_id: str, n: int = 20) -> Optional[list[str]]:
        """The most relevant top-level comments on one video. 1 unit.

        Comments are the closest thing to audience data the public API offers:
        the language viewers write in, where they say they are, what stage of
        life they mention. None means comments are off or unreachable, which
        is missing evidence, never a negative signal.
        """
        h = _hash("comments", video_id, n)
        hit = cache.get_search(h)
        if hit is not None:
            return hit or None
        try:
            data = await self._get(
                "commentThreads",
                {
                    "part": "snippet",
                    "videoId": video_id,
                    "order": "relevance",
                    "maxResults": n,
                    "textFormat": "plainText",
                    "fields": "items/snippet/topLevelComment/snippet/textOriginal",
                },
                units=1,
            )
        except YouTubeError:
            cache.put_search(h, [])
            return None
        texts = [
            it.get("snippet", {}).get("topLevelComment", {})
              .get("snippet", {}).get("textOriginal", "")
            for it in data.get("items", [])
        ]
        texts = [t for t in texts if t]
        cache.put_search(h, texts)
        return texts or None


def _reason(resp: httpx.Response) -> str:
    try:
        errs = resp.json().get("error", {}).get("errors", [])
        if errs:
            return errs[0].get("reason", "")
        return resp.json().get("error", {}).get("status", "")
    except Exception:
        return ""
