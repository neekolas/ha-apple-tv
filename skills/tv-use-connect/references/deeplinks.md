# Deep links by service

Which URL templates open which services through `tv_play {"link": "..."}`, and how far each got on a real Apple TV.

Trials on November 19, 2026 used tvOS 27.2 (24K122) and US and Canadian catalog examples. Access depends on sign-in, region and entitlements. For the trial results, see [docs/deeplinks.md](https://github.com/neekolas/tv-use/blob/main/docs/deeplinks.md) in the bridge repository, and the machine-readable [deeplinks.validated.yaml](https://github.com/neekolas/tv-use/blob/main/app/atv_bridge_app/data/deeplinks.validated.yaml).

Levels: **A** the app opened (possibly at a welcome or profile screen), **B** the title page appeared, **C** the title played.

These are observed outcomes for specific titles. Current launches can also answer recognized profile and privacy prompts. See [native app controls](native-apps.md).

## Confirmed

| Service | URL template | Level | How to get the ID | Notes |
|---|---|---|---|---|
| Bloomberg | `https://www.bloomberg.com/live/us` | A | Fixed URL for the US live feed | Opens the Home tab with the live program. A tracking prompt appeared on one launch. |
| Disney+ | Apple TV handoff (below) | C | Apple TV catalog URL | Apple handed the tested title to Disney+. Check `data.landed` and `screen.playing` after the handoff. |
| HBO Max | `https://play.hbomax.com/video/watch/{id}` | C | Episode UUID after `video/watch/` in the HBO Max web URL | A profile picker appeared first. After a profile was chosen, the link played. |
| Hulu | `hulu://series/{id}` | B | UUID at the end of the hulu.com series URL | The trial showed “Open in Hulu?” first. |
| Music | `https://music.apple.com/us/album/{slug}/{id}` | B | Slug and numeric album ID from a Music share URL | Opens the album. Select `Play` to start it. |
| NBC | Apple TV handoff (below) | C | Apple TV catalog URL | A tracking prompt appeared on one launch. |
| Netflix | `nflx://www.netflix.com/title/{id}` | A | Digits after `title/` in a Netflix share link | "Open in Netflix?" dialog, then a profile picker. Lands on Netflix Home, not the title. Search from there. |
| Peacock | Apple TV handoff (below) | B | Apple TV catalog URL | The TV app shows `Open In...`. Then a Peacock profile picker. The title page offered "Upgrade to Watch". |
| Podcasts | `https://podcasts.apple.com/us/podcast/{slug}/id{id}` | A | Slug and the digits after `id` from a Podcasts share URL | First run showed a welcome screen with `Continue`. |

**Apple TV handoff** `https://tv.apple.com/{region}/{kind}/{slug}/{id}?action=play`. Find the title on tv.apple.com for the user's country. Copy the whole show or movie URL: `{region}` is like `us`, `{kind}` is `show` or `movie`, and `{id}` starts with `umc.cmc.`. The bridge adds `action=play` itself. Apple chooses the provider. The bridge can verify a provider handoff for an Apple title ID recorded in that provider’s confirmed templates. Other titles can return `uncertain` even if a provider opens. Read `screen.app` before sending another action.

The trials also recorded these routes.

- Disney+: `https://www.disneyplus.com/browse/entity-{id}` reached the title page (B).
- HBO Max: `https://play.hbomax.com/show/{id}` stopped at the profile picker (A).
- Hulu: the Apple TV handoff reached the series page (B). `hulu://watch/{id}` stopped on the series page (B).
- NBC: `https://www.nbc.com/{id}` opened the Shows A–Z list (A). `nbctve://live/{id}` asked for TV-provider access (A).
- Peacock: `https://www.peacocktv.com/watch/asset/tv/{slug}/{id}` opened Peacock Home (A).

## To verify

**The TV refused the web URL** (`launch_refused`): ABC News Live, ABC, CBC News, CNN, Calm, Comedy Central, FOX One, Fox News, Global TV, Instagram, MUBI, NBC News, NBC Sports, NFL, Paramount+. Use `tv_open` with `search` instead. Apple TV handoffs for some of these went elsewhere: to another app, a TV-provider sign-in, "Content Unavailable", or playback inside the TV app.

**FXNOW** also refused its web URL. The Apple TV handoff played the title. The trial did not identify the provider.

**Not tried yet** (researched candidates):

| Service | Candidate template | ID |
|---|---|---|
| Prime Video | `https://www.primevideo.com/detail/{id}` | Season detail ID; keep the leading zero |
| Spotify | `spotify:?uri=spotify:album:{id}&play=true` | Base62 album ID from a share link |
| Sundance Now | `https://www.sundancenow.com/series/watch/{slug}/{id}` | Slug and season ID |
| TV (Apple TV app) | `https://tv.apple.com/us/show/{slug}/{id}?action=play` | Slug and `umc.cmc.` ID |
| VLC | `vlc://{id}` | A media URL the TV can reach, keeping its `https://` |
| Vevo | `https://www.hq.vevo.com/watch` | None |
| Vimeo | `https://vimeo.com/{id}` | Numeric video ID |
| YouTube Kids | `https://www.youtubekids.com/watch?v={id}` | The `v` parameter |
| YouTube | `https://www.youtube.com/watch?v={id}` | Prefer `tv_play {"youtube":"<video ID>"}`, the supported path |
| Fitness | `https://fitness.apple.com/us/workout/{slug}/{id}` | Slug and numeric workout ID |
| Kanto | `https://kanto.co/openApp` | None. Candidate for opening the home screen. |
| Smule | `https://www.smule.com/recording/{slug}/{id}` | Slug and combined recording ID |

**App launch only** (no content link found). Use `tv_open {"app": "<bundle ID>"}`: Photos `com.apple.TVPhotos`, Arcade `com.apple.Arcade`, Jeopardy `com.frostypop.jeopardy`, Family Feud `com.gameloft.ffp`, VIFF Connect `com.cinesend.viff`, Stellar `com.stellartix`, Computers `com.apple.TVHomeSharing`.

## Skipped

Crave, Criterion and Spectrum TV were excluded from the final results.
