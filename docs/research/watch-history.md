# Watch history: what the Apple TV reports

This note records what the bridge can learn about playing media, and how the watch history uses it. The feature is `tv_history`. All titles and names in this note are made-up examples. [Tool reference](../mcp-api.md#tv_history) · [Recorder](../../app/atv_bridge_app/history.py)

## Findings

- The TV pushes now-playing state to the bridge. The bridge does not need to poll the TV.
- Most apps report a title. Hulu, Disney+, HBO Max, NBC, Music and YouTube did. Netflix reported only the playing state.
- Each app formats the title in its own way. The recorder parses the three observed formats into series, season and episode.
- A finished item can stay in now-playing for hours with state `idle`, `paused` or `stopped`. Only active playback counts as watching.
- The title can arrive after playback starts. On YouTube it sometimes arrived only at the first pause.
- The extra fields (content identifier, series, season, episode, genre, store ID) are **not measured yet**. See [Not measured](#not-measured).

## Where the data comes from

| Source | Transport | Cost | Used by the recorder |
| --- | --- | --- | --- |
| MRP now-playing state | Pushed by the TV over the AirPlay session the bridge already holds | None. pyatv keeps the state in memory. | Yes |
| MRP `ContentItemMetadata` extra fields | Same messages | None | An allowlist of descriptive fields, stored in `meta` |
| Companion `FetchCurrentNowPlayingInfoEvent` | One request per read | One round trip | No. Experimental in the fork and not measured. |
| Accessibility labels (`tv_look`) | Remote Pairing tunnel and a second process | A tunnel and a read per sample | No |
| Screenshots | DVT capture | Shows a toast on the TV | No |

The TV sends `SetStateMessage`, `UpdateContentItemMessage`, `SetNowPlayingClientMessage` and `SetNowPlayingPlayerMessage` when playback changes. pyatv's `PlayerStateManager` applies each message to its cached state. `Metadata.playing()` and `Metadata.app` read that cache. They send nothing. [Player state](../../vendor/pyatv/pyatv/protocols/mrp/player_state.py) · [Playing fields](../../vendor/pyatv/pyatv/protocols/mrp/__init__.py)

The engine already reads this cache every two seconds and publishes a change event when the snapshot differs. The recorder listens for that event. So a play, pause or new item reaches the recorder within about two seconds, with no extra traffic. [Engine monitor](../../app/atv_bridge_app/engine.py)

pyatv exposes these fields on `Playing`: `title`, `artist`, `album`, `genre`, `series_name`, `season_number`, `episode_number`, `content_identifier`, `itunes_store_identifier`, `media_type`, `device_state`, `position` and `total_time`. The app is the now-playing client: a bundle ID and a display name. The MRP message has about 80 more fields. [Message definition](../../vendor/pyatv/pyatv/protocols/mrp/protobuf/ContentItemMetadata.proto) · [pyatv supported features](https://pyatv.dev/documentation/supported_features/)

Accessibility is a poor fallback for history. A video player shows few labels while it plays. Each read needs the Remote Pairing tunnel. The recorder does not use it. An agent that needs the app in front can still call `tv_look`.

## Observed on hardware

Method: the bridge's Home Assistant media players record `title`, `artist`, state and app on every change. I read that recorder history for two TVs: an Apple TV 4K 2nd generation on tvOS 27.2 and an Apple TV 4K 1st generation on tvOS 26.6.1. The values come from the same `Session.status()` read that the recorder uses. I also made text-only `tv_look` reads. The table keeps only the shape of each app's fields. The examples are invented.

| App | Bundle ID | `title` shape | `artist` shape |
| --- | --- | --- | --- |
| Hulu | `com.hulu.plus` | Series, season, episode and episode name in one string: `Example Show \| S1 E2 - Sample Episode` | empty |
| Disney+ | `com.disney.disneyplus` | Series: `Example Kids Show` | Season, episode and episode name: `S1:E2 Sample Episode` |
| HBO Max | `com.wbd.stream` | Episode name: `Sample Episode`. Sometimes empty. | Series: `Example Drama` |
| NBC | `com.nbcuni.nbc.portal` | Series only: `Example Late Show` | empty |
| Music | `com.apple.TVMusic` | Song: `Sample Song` | Artist: `Sample Artist` |
| YouTube | `com.google.ios.youtube` | Video title: `Sample Video`. Often empty for the first seconds. | Channel: `Sample Channel` |
| Netflix | `com.netflix.Netflix` | empty | empty |
| AirPlay from the bridge | `com.apple.TVAirPlay` | empty | empty |

Other observations:

- **Stale items.** A finished song stayed in now-playing for many hours with state `idle`, while the front app changed many times. A paused episode stayed while Settings, Home and the screen saver were in front. The recorder must not count these states.
- **Late titles.** YouTube reported `playing` with an empty title, then the title and channel at the first pause some seconds later. The recorder opens a provisional entry without a title. It fills the entry when the title arrives, if the titled item's position fits the untitled time.
- **The front app is not the media app.** The app that Home Assistant recorded is the front app from accessibility. It was empty during YouTube playback and showed Settings over a paused Hulu item. The recorder uses the now-playing client, which is the app that owns the media. A later passive read showed `com.google.ios.youtube` as the now-playing client.
- **Short `playing` states.** Settings, the screen saver and AirPlay reported `playing` for two or three seconds with an empty title. The recorder drops untitled playback in system apps and anything shorter than 10 seconds.
- **Netflix.** A long Netflix session reported the playing state and no title or artist.

## Not measured

I could not read the extra fields on hardware. No lab pairing exists on the workstation, and creating one shows a PIN on the TV. Starting playback was out of scope. The running bridge exposes only title, artist, state, position and duration through its read-only tools.

These questions are open until the first deploy of this change. `data.now` is a live read of the cache, so the checks are read-only.

| Question | How to answer it |
| --- | --- |
| Does YouTube put the video ID in `contentIdentifier`? The bridge's playback check already expects that. | Play a video for 10 seconds. Call `tv_history {"limit":1}`. Read `data.now[0].content_id` and the entry's `url`. Both are absent when the identifier holds no video ID. |
| Which apps fill `seriesName`, `seasonNumber`, `episodeNumber`, `genre` and `iTunesStoreIdentifier`? The Apple TV app is the likely one. | Play an episode in each app. Read `data.now[0]`. |
| Which allowed extra fields does each app set, such as `subtitle` or `serviceIdentifier`? | Read `data.now[0].meta`. |
| Does Netflix report a distinct queue item (`item_id`) or duration per episode? Without one, a Netflix session is one entry. | Play two Netflix episodes. Compare `item_id` and `duration_s`. |
| Does the queue item (`item_id`) stay the same when a late title arrives? Without a matching queue item, an app whose position does not advance leaves its untitled time in an untitled entry. | Start a YouTube video. Compare `data.now[0].item_id` before and after the title appears. |
| Is the now-playing client always the media app on tvOS 27.2? | Compare `data.now[0].app_id` with the app on screen. |
| Does an app report position 0 for a moment when it resumes an item? Then a resume past half would start a new entry. | Play an episode past its middle, leave it and resume it. Count its entries. |
| Does YouTube report an untitled advertisement with its own position before a video? Then each advertisement of more than 10 seconds is an untitled entry. | Start a video that has an advertisement. Read `data.now[0]` during it and the entries after it. |

## Design that follows

- **Sampling.** Each sample reads memory. It takes no TV lock and sends nothing. The recorder samples on every published change and on a timer. After a change the timer waits `history_fast_interval` (15 s), then doubles the wait up to `history_interval` (300 s). The timer is a heartbeat: it saves progress and catches a missed event.
- **Watched time.** The recorder credits the time between two readings only when both were playing the same entry. When playback stops or changes, it credits the time up to the engine's change event and nothing after it. Without an event it credits nothing, except for an item that restarted without a break. So when a sample reads a stop before the engine reports it, the time since the previous sample is lost. That is at most one `history_interval` for each stop. The loss is known and accepted: the recorder cannot tell when in that time playback stopped, and it does not guess. It never uses the position to count time, so a scrub while paused adds nothing. Elapsed time comes from the monotonic clock. The wall clock is used only for `start` and `end`, because the Home Assistant Green has no battery clock and NTP can step the time after boot. A gap longer than two intervals counts as a stall and adds nothing. pyatv reports any playback rate other than 0 or 1 as `seeking`, so `seeking` counts as playing.
- **Entries.** A reading belongs to an entry unless a field that both report differs: app, title, artist, album, series, season, episode, content identifier or duration. Without a title on either side, the queue item must also match.
- **Untitled playback.** An untitled reading never extends a titled entry, unless both carry the same queue item. It opens a provisional entry. When a title arrives, the provisional entry takes it only if the titled item can be what played untitled. That needs one of three cases: both readings carry the same queue item, the titled reading has no position, or its position is at least the provisional entry's playing time less 10 seconds. The playing time is the watched time plus the time since the previous reading, if that reading was playing. In any other case the titled item started later. The provisional entry then closes without a title, or is dropped if it has less than 10 seconds, and the titled item starts its own entry. If the provisional entry takes the title and that item already has an open entry, the provisional entry folds into it. Untitled playback continues only after a break of at most 15 minutes.
- **Returning items.** An item that plays again within four hours continues its entry, also after other items (A, B, A gives two entries). The recorder checks the last ten entries. After four hours it is a new entry.
- **Rewatch.** A reading in the first tenth of the duration is a restart. After a break, a restart is a new entry when the entry's last position was at least half of the duration. A break is a reading that was not this item in the state `playing`, `paused`, `seeking` or `loading`: another item, an idle TV or a lost session. A bridge restart is also a break. Without a break, a restart is a new entry only when the entry had reached its last tenth. So a scrub back to the start from before the last tenth continues the entry. This case still needs a reading in the last tenth, so an item that repeats without a break can continue one entry. The entry's last position is the last position read. When playback stops or the item changes, the recorder adds the playing time that it credits after that reading, up to the duration. So after a break the rule does not need a sample near the end of the item. The rule needs a duration and both positions. Without them the item continues its entry.
- **Queue item.** The recorder reads the MRP queue item identifier directly. pyatv's `Playing.hash` is not usable: without an identifier it returns a digest of the title, which changes when a late title arrives.
- **What is left out.** States other than `playing` and `seeking`, a TV that is off, the bridge's own pages and AirPlay clips, untitled playback in system apps, and plays shorter than 10 seconds.
- **Privacy.** `meta` is an allowlist of descriptive fields. The recorder never stores `assetURLString`, artwork or lyrics links, `profileIdentifier`, subscription identifiers or binary fields. It drops any allowed value that contains a link. A content identifier is stored only when it is a plain token: 1 to 200 letters, digits, `.`, `_`, `:` and `-`. A link, with or without a scheme, is not such a token and is dropped. On YouTube the recorder stores only the 11-character video ID (letters, digits, `_` and `-`), from a bare ID or from a link that holds one. The `url` field is built from that checked ID alone. Only the admin-token MCP and REST routes serve the history. The Home Assistant LLM tools and Assist do not.
- **Storage.** One JSON-lines file per TV at `/data/tvs/<id>/history.jsonl`, mode 0600, capped at `history_max_entries` (2,000). A later line with the same ID replaces the earlier one. The open entry is written at most once per `history_interval`, and when playback stops. The file is rewritten only when it holds twice the cap in lines. Loading skips lines that are not valid entries. File access runs on a worker thread. The read of a sample can be cancelled. The work after the read runs to its end, also when its caller is cancelled. The thread cannot be stopped, so the sample keeps its lock until the write ends and a later write cannot race a compaction. With `watch_history` off, the file is not read or written. Removing a TV deletes its file.
