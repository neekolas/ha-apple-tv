# Apple TV deep links by service

Tested November 19, 2026 on an Apple TV 4K (2nd generation) running tvOS 27.2 (24K122). 9 services confirmed, 35 to verify, and 3 skipped. The catalog covers 47 services.

## To verify

ABC News Live, ABC, CBC News, CNN, Calm, Comedy Central, FOX One, FXNOW, Fox News, Global TV, Instagram, MUBI, NBC News, NBC Sports, NFL, Paramount+, Prime Video, Spotify, Sundance Now, TV (Apple TV app), VLC, Vevo, Vimeo, YouTube Kids, YouTube, Fitness, Photos, Arcade, Kanto, Smule, Jeopardy, Family Feud, VIFF Connect, Stellar, Computers.

## Results

**A** means the requested app opened, including a welcome or profile screen. **B** means its title page appeared. **C** means the target played in that app. Levels describe the recorded trials, including any prompt handling described below. The run ended before the three-repeat consistency pass. The region identifies the catalog used for that example. Account region and entitlements may differ.

The preferred template appears first in the machine-readable catalog. Unconfirmed rows contain a researched candidate, including forms the bridge refused. Seven apps have only a bundle-ID launch fallback. The trial table records earlier observations for services later marked skipped.

| Service | Catalog region | Trial result | Best URL or fallback | How to get the ID | Landing or blocker |
|---|---|---|---|---|---|
| ABC News Live | US | To verify | `https://abcnews.com/live` | On ABC News choose Live. The channel uses a fixed path. [ABC News Live](https://abcnews.com/live) | Companion refused the URL. The TV stayed on Home. Legacy ABC News hostname was refused. The TV stayed on Home. |
| ABC | US | To verify | `https://abc.com/shows/{id}` | Copy the last path segment from the ABC show page. Example `abbott-elementary`. [Abbott Elementary](https://abc.com/shows/abbott-elementary) | Companion refused the URL. The TV stayed on Home. Apple routed the title to Hulu, which displayed playback error P-DEV318. The ABC app did not open. |
| Bloomberg | US | Confirmed A | `https://www.bloomberg.com/live/us` | Open Bloomberg Live, select the US feed and copy the channel path. Example `live/us`. [Bloomberg Television](https://www.bloomberg.com/live/us) | Bloomberg opened its Home tab with the current live program. The earlier tracking prompt was absent on this launch. Tracking prompt appeared. No choice was recorded. It was absent on relaunch. |
| CBC News | CA | To verify | `https://www.cbc.ca/news/{id}` | Open The National from CBC News. Copy its section path. CBC Gem is a separate app. Example `thenational`. [The National](https://www.cbc.ca/news/thenational) | Companion refused the URL. The TV stayed on Home. Canadian Apple TV URL stopped at Content Unavailable in TV. CBC News was absent. |
| CNN | US | To verify | `https://www.cnn.com/live` | Select Watch Live TV on CNN. Copy the live hub path. [CNN Live](https://www.cnn.com/live) | Companion refused the URL. The TV stayed on Home. |
| Calm | US/CA | To verify | `https://www.calm.com/program/{id}/{slug}` | Open Calm’s Blue Gold article and copy the linked program URL. Keep both the case-sensitive ID and slug. Example `BVLV98v`. [Blue Gold](https://www.calm.com/program/BVLV98v/blue-gold) | Companion refused the URL. The TV stayed on Home. |
| Comedy Central | US | To verify | `https://www.cc.com/fan-hub/{id}` | Open The Daily Show on Comedy Central and copy the fan-hub slug. Example `the-daily-show`. [The Daily Show](https://www.cc.com/fan-hub/the-daily-show) | Companion refused the URL. The TV stayed on Home. Apple handoff ended at a Who’s Watching picker with an Unknown Error dialog. The provider was unidentified and the requested show never appeared. |
| Crave | CA | Skipped | — | Open the 2013 short on Crave Canada. Copy the entire last segment including the number. | Excluded from the final results. |
| Criterion | US/CA | Skipped | — | Open the film in the Criterion Channel collection and copy both slugs from the video URL. | Excluded from the final results. |
| Disney+ | US | Confirmed C | `https://tv.apple.com/{region}/{kind}/{slug}/{id}?action=play` | Search the listed country's tv.apple.com catalog for the same title. Copy the entire show or movie URL, including its umc.cmc ID. Example `umc.cmc.3ez94w3cvm4oeo9rugqsu6dx1`. [Bluey](https://tv.apple.com/us/show/bluey/umc.cmc.3ez94w3cvm4oeo9rugqsu6dx1?action=play) | Apple handed Bluey to Disney+. Now Playing reported Bluey and the DRM-black image showed its captions. |
| FOX One | US | To verify | `https://www.fox.com/detail/series/{id}/{slug}` | Copy the SER identifier and following slug from the FOX One series page. Example `SER000878XWTP`. [The Masked Singer](https://www.fox.com/detail/series/SER000878XWTP/the-masked-singer) | Companion refused the URL. The TV stayed on Home. Apple routed the series to Spectrum’s TV-provider sign-in. No account was selected and FOX One was not foregrounded. |
| FXNOW | US | To verify | `https://tv.apple.com/{region}/{kind}/{slug}/{id}?action=play` | Search the listed country's tv.apple.com catalog for the same title. Copy the entire show or movie URL, including its umc.cmc ID. Example `umc.cmc.javg04xbn3eonbgfvnaqmodk`. [The Bear](https://tv.apple.com/us/show/the-bear/umc.cmc.javg04xbn3eonbgfvnaqmodk?action=play) | Companion refused the URL. The TV stayed on Home. The Bear S5 E4 played with captions, but neither MCP nor the image identified the provider. This confirms title playback only. |
| Fox News | US | To verify | `https://www.foxnews.com/video/{id}` | Open Watch Live on Fox News and copy the numeric video path for the channel. Example `5614615980001`. [Fox News Channel](https://www.foxnews.com/video/5614615980001) | Companion refused the URL. The TV stayed on Home. |
| Global TV | CA | To verify | `https://www.globaltv.com/shows/{id}/` | Open Shows on Global TV Canada and copy the show slug. Example `survivor`. [Survivor](https://www.globaltv.com/shows/survivor/) | Companion refused the URL. The TV stayed on Home. Apple handed Survivor to Spectrum’s TV-provider sign-in. Global TV did not open. |
| HBO Max | US | Confirmed C | `https://play.hbomax.com/video/watch/{id}` | Open the HBO Max episode offer and copy the UUID after video/watch/. Example `e457df99-f817-4646-b64c-8ce7afaeb405`. [Celebration](https://play.hbomax.com/video/watch/e457df99-f817-4646-b64c-8ce7afaeb405) | HBO Max played Celebration after a profile was selected. The episode link worked on relaunch. |
| Hulu | US | Confirmed B | `hulu://series/{id}` | Copy the final UUID from the Hulu series URL. Example `ef31c7e1-cd0f-4e07-848d-1cbfedb50ddf`. | Confirmed Open in Hulu. The series page offered S1 E1 True Crime in the Only Murders catalog. |
| Instagram | US | To verify | `https://www.instagram.com/{id}/` | Open the creator profile and copy its handle without the @ prefix. Example `natgeo`. [National Geographic](https://www.instagram.com/natgeo/) | Companion refused the creator profile URL. The TV stayed on Home. |
| MUBI | US | To verify | `https://mubi.com/en/us/films/{id}` | Copy the film slug from the US MUBI catalog. Verify the year is 2024. Example `the-substance`. [The Substance](https://mubi.com/en/us/films/the-substance) | Companion refused the MUBI catalog URL. The TV stayed on Home. The Apple handoff showed a black frame with stale paused metadata. Back revealed Hulu on its previous series page. |
| Music | US | Confirmed B | `https://music.apple.com/us/album/{slug}/{id}` | Share the Fleetwood Mac album in Music and copy the slug and numeric album ID. Example `594061854`. [Rumours](https://music.apple.com/us/album/rumours/594061854) | The album URL opened Rumours. Selecting Play separately played Second Hand News at full track length. |
| NBC News | US | To verify | `https://www.nbcnews.com/tab/watch` | Use the fixed Watch tab path in NBC News’s association file. [NBC News NOW](https://www.nbcnews.com/tab/watch) | Companion refused the NBC News NOW URL. The TV stayed on Home. Companion refused the association file’s Watch tab URL. The TV stayed on Home. |
| NBC Sports | US | To verify | `https://www.nbcsports.com/watch/{id}` | Choose Premier League under NBC Sports Watch and copy the sports hub slug. Example `premier-league`. [Premier League](https://www.nbcsports.com/watch/premier-league) | Companion refused the Premier League hub URL. The TV stayed on Home. |
| NBC | US | Confirmed C | `https://tv.apple.com/{region}/{kind}/{slug}/{id}?action=play` | Search the listed country's tv.apple.com catalog for the same title. Copy the entire show or movie URL, including its umc.cmc ID. Example `umc.cmc.3p5hey1eeuk85gse9d27zaa5q`. [Saturday Night Live](https://tv.apple.com/us/show/saturday-night-live/umc.cmc.3p5hey1eeuk85gse9d27zaa5q?action=play) | Apple handed Saturday Night Live to NBC and playback started. The earlier tracking prompt was absent. Free and account access could not be distinguished. Tracking prompt appeared. No choice was recorded. It was absent on relaunch. |
| NFL | US | To verify | `https://www.nfl.com/network` | Choose NFL Network on NFL.com and copy the channel path. [NFL Network](https://www.nfl.com/network) | Companion refused the NFL Network page. The TV stayed on Home. Companion refused the NFL clip URL. The TV stayed on Home. Companion refused the nflmobile live scheme. The TV stayed on Home. |
| Netflix | US/CA | Confirmed A | `nflx://www.netflix.com/title/{id}` | Search Netflix for the show and copy the digits after title/ from its share link. Example `80057281`. | Confirmed Open in Netflix and chose a profile. Netflix opened Home with a different featured series. |
| Paramount+ | US | To verify | `https://www.paramountplus.com/shows/{id}/` | Open the show on Paramount+ and copy its shows/ slug. Example `star-trek-strange-new-worlds`. [Star Trek: Strange New Worlds](https://www.paramountplus.com/shows/star-trek-strange-new-worlds/) | Companion refused the Paramount+ show URL. The TV stayed on Home. S1 E1 played in Apple TV. Back showed TV’s Try Paramount+ Free offer. The standalone Paramount+ app was not observed. Companion refused the Paramount+ episode URL. The TV stayed on Home. |
| Peacock | US | Confirmed B | `https://tv.apple.com/{region}/{kind}/{slug}/{id}?action=play` | Search the listed country's tv.apple.com catalog for the same title. Copy the entire show or movie URL, including its umc.cmc ID. Example `umc.cmc.455js879szmdywutf3qjewagm`. [The Office](https://tv.apple.com/us/show/the-office/umc.cmc.455js879szmdywutf3qjewagm?action=play) | Apple TV required Open In. Selected a Peacock profile and found The Office S9 E3 with Upgrade to Watch. No upgrade was selected. |
| Podcasts | US | Confirmed A | `https://podcasts.apple.com/us/podcast/{slug}/id{id}` | Share the NPR show in Podcasts and copy the slug and digits following id. Example `290783428`. [Planet Money](https://podcasts.apple.com/us/podcast/planet-money/id290783428) | The Planet Money URL opened the Apple Podcasts welcome screen. The run ended before Continue was selected. |
| Prime Video | US/CA | To verify | `https://www.primevideo.com/detail/{id}` | Copy the season detail ID from Prime Video. Keep the leading zero. Example `0S1FYJ3LY9KTL9C7WFFAGA9F6F`. [The Boys](https://www.primevideo.com/detail/0S1FYJ3LY9KTL9C7WFFAGA9F6F) | No live trial. |
| Spectrum TV | US | Skipped | — | Choose Live TV in Spectrum Watch. This hub uses a fixed path. | Excluded from the final results. |
| Spotify | US/CA | To verify | `spotify:?uri=spotify:album:{id}&play=true` | Use Share → Copy album link on Fleetwood Mac’s Rumours and copy the base62 segment after album/. Example `1bt6q2SruMsBtcerNVtpZB`. | No live trial. |
| Sundance Now | US | To verify | `https://www.sundancenow.com/series/watch/{slug}/{id}` | Open season 1 in the Sundance Now catalog and copy the slug and season identifier. Example `4680272`. [A Discovery of Witches](https://www.sundancenow.com/series/watch/a-discovery-of-witches/4680272) | No live trial. |
| TV (Apple TV app) | US | To verify | `https://tv.apple.com/us/show/{slug}/{id}?action=play` | Share the show from the US Apple TV storefront and copy the slug and full umc.cmc identifier. Example `umc.cmc.vtoh0mn0xn7t3c643xqonfzy`. [Ted Lasso](https://tv.apple.com/us/show/ted-lasso/umc.cmc.vtoh0mn0xn7t3c643xqonfzy?action=play) | No live trial. |
| VLC | US/CA | To verify | `vlc://{id}` | Prefix the complete receiver-accessible media URL with vlc://. Preserve its https://. Example `https://media.w3.org/2010/05/bunny/trailer.mp4`. | No live trial. |
| Vevo | US | To verify | `https://www.hq.vevo.com/watch` | Open Where to Watch on Vevo. This directory has no individual video ID. [Vevo](https://www.hq.vevo.com/watch) | No live trial. |
| Vimeo | US/CA | To verify | `https://vimeo.com/{id}` | Share Terje Sørgjerd’s film on Vimeo and copy the numeric video ID. Example `22439234`. [The Mountain](https://vimeo.com/22439234) | No live trial. |
| YouTube Kids | US/CA | To verify | `https://www.youtubekids.com/watch?v={id}` | Copy the v parameter for Pinkfong’s original Baby Shark Dance. Kids availability depends on the age filter. Example `XqZsoesa55w`. [Baby Shark Dance](https://www.youtubekids.com/watch?v=XqZsoesa55w) | No live trial. |
| YouTube | US/CA | To verify | `https://www.youtube.com/watch?v={id}` | Copy the 11-character v parameter or youtu.be path from jawed’s video. Example `jNQXAC9IVRw`. [Me at the zoo](https://www.youtube.com/watch?v=jNQXAC9IVRw) | No live trial. |
| Fitness | US | To verify | `https://fitness.apple.com/us/workout/{slug}/{id}` | Share the workout in Fitness+ and copy its slug and numeric workout ID. Example `1595101770`. [Yoga with Jessica](https://fitness.apple.com/us/workout/yoga-with-jessica/1595101770) | No live trial. |
| Photos | US/CA | To verify | App launch only: `tv_open {"app":"com.apple.TVPhotos"}` | Select a shared album in Photos. | No live trial. |
| Arcade | US/CA | To verify | App launch only: `tv_open {"app":"com.apple.Arcade"}` | The public catalog links to game App Store pages. No Arcade content URL identified. | No live trial. |
| Kanto | US/CA | To verify | `https://kanto.co/openApp` | Use the fixed /openApp path from Kanto’s association file to request its home screen. [Kanto](https://kanto.co/openApp) | No live trial. |
| Smule | US/CA | To verify | `https://www.smule.com/recording/{slug}/{id}` | Copy the recording’s slug and combined performer_recording ID from the shared performance. Example `2476974843_3717818605`. [Bohemian Rhapsody](https://www.smule.com/recording/rhapsody-bohemian-rhapsody-bohemian/2476974843_3717818605) | No live trial. |
| Jeopardy | US/CA | To verify | App launch only: `tv_open {"app":"com.frostypop.jeopardy"}` | No public TV game-session link identified. | No live trial. |
| Family Feud | US/CA | To verify | App launch only: `tv_open {"app":"com.gameloft.ffp"}` | No public TV game-session link identified. | No live trial. |
| VIFF Connect | CA | To verify | App launch only: `tv_open {"app":"com.cinesend.viff"}` | Find the festival film on VIFF and use its screening access link. No public TV title link was found for an active screening. | No live trial. |
| Stellar | US | To verify | App launch only: `tv_open {"app":"com.stellartix"}` | Stellar’s Your Tickets page opens the purchased event. No public watch ID was tested. | No live trial. |
| Computers | US/CA | To verify | App launch only: `tv_open {"app":"com.apple.TVHomeSharing"}` | Choose a title in your Home Sharing library. | No live trial. |

## Running a link

Use `tv_play {"tv":"<room>","link":"<URL>"}`. For an app by name or bundle ID, use `tv_open`. For a YouTube video, prefer `tv_play` with `youtube`. For an Apple catalog handoff, copy the full regional title URL. The bridge sets `action=play` itself. It accepts the TV app as a target and adds a provider as a verifiable destination only for an Apple title ID recorded in that provider's confirmed templates.

On success, check `data.landed`, `screen.app` and the title in `screen.playing`. A title page may offer Play, Open In, Rent or Upgrade. Inspect the label before selecting it. A different provider can open, and the bridge can return `uncertain` even after a handoff. The bridge has no purchase guard. See [the public API](mcp-api.md).

## Trial evidence

The table summarizes the recorded trials. The catalog records the template, region and best observed level. Raw screenshots and MCP captures were local test artifacts. A separate Play action earns the link level B. These historical outcomes can differ from the current tool's `data.landed`.

| Service | URL sent | Observed result | Notes |
|---|---|---|---|
| ABC News Live | `https://abcnews.com/live` | failed | Companion refused the URL. The TV stayed on Home. |
| ABC News Live | `https://abcnews.go.com/Live` | failed | Legacy ABC News hostname was refused. The TV stayed on Home. |
| ABC | `https://abc.com/shows/abbott-elementary` | failed | Companion refused the URL. The TV stayed on Home. |
| ABC | `https://tv.apple.com/us/show/abbott-elementary/umc.cmc.4yajt40knug1iwx1l9hrjxkxy?action=play` | failed | Apple routed the title to Hulu, which displayed playback error P-DEV318. The ABC app did not open. |
| Bloomberg | `https://www.bloomberg.com/live/us` | app_home | Bloomberg opened its Home tab with a tracking-permission prompt. Neither permission option was selected. |
| Bloomberg | `https://www.bloomberg.com/live/us` | app_home | Bloomberg opened its Home tab with the current live program. The earlier tracking prompt was absent on this launch. |
| CBC News | `https://www.cbc.ca/news/thenational` | failed | Companion refused the URL. The TV stayed on Home. |
| CBC News | `https://tv.apple.com/ca/show/the-national/umc.cmc.3p14fkx3jnc8b0vi1dipt1ylt?action=play` | failed | Canadian Apple TV URL stopped at Content Unavailable in TV. CBC News was absent. |
| CNN | `https://www.cnn.com/live` | failed | Companion refused the URL. The TV stayed on Home. |
| Calm | `https://www.calm.com/program/BVLV98v/blue-gold` | failed | Companion refused the URL. The TV stayed on Home. |
| Comedy Central | `https://www.cc.com/fan-hub/the-daily-show` | failed | Companion refused the URL. The TV stayed on Home. |
| Comedy Central | `https://tv.apple.com/us/show/the-daily-show/umc.cmc.1wp3e52ah4t1khqohadjagrei?action=play` | failed | Apple handoff ended at a Who’s Watching picker with an Unknown Error dialog. The provider was unidentified and the requested show never appeared. |
| Crave | `https://www.crave.ca/en/special/letterkenny-problems-35502` | app_home | Crave stopped at Bell Media’s Privacy Notice. Accept and Manage my privacy settings were left untouched. |
| Crave | `https://tv.apple.com/ca/movie/letterkenny-problems/umc.cmc.1bn56uosz50tfde6bseq3n9k8?action=play` | failed | The Canada Apple TV link stayed in TV and displayed Content Unavailable. The error did not identify a region or network cause. |
| Crave | `https://www.crave.ca/en/play/letterkenny/aint-no-reason-to-get-excited-s1e1-782272` | app_home | Crave stopped at Bell Media’s Privacy Notice. Accept and Manage my privacy settings were left untouched. |
| Criterion | `https://www.criterionchannel.com/seven-samurai/videos/seven-samurai` | failed | Companion refused the URL. The TV stayed on Home. |
| Criterion | `https://tv.apple.com/us/movie/seven-samurai/umc.cmc.3j1e4hzd95tf9vtnjot79gwbp?action=play` | failed | Apple handed Seven Samurai to HBO Max’s profile picker. Criterion did not open. |
| Criterion | `https://ca.criterionchannel.com/films/462qqAgx/seven-samurai` | app_home | The Canadian film URL opened Criterion’s Home carousel. Seven Samurai was absent and the app offered Log In. |
| Disney+ | `https://www.disneyplus.com/browse/entity-fa6973b9-e7cf-49fb-81a2-d4908e4bf694` | title_page | Disney+ displayed Bluey with Play, Trailer and its episode list. |
| Disney+ | `https://tv.apple.com/us/show/bluey/umc.cmc.3ez94w3cvm4oeo9rugqsu6dx1?action=play` | playing | Apple handed Bluey to Disney+. Now Playing reported Bluey and the DRM-black image showed its captions. |
| FOX One | `https://www.fox.com/detail/series/SER000878XWTP/the-masked-singer` | failed | Companion refused the URL. The TV stayed on Home. |
| FOX One | `https://tv.apple.com/us/show/the-masked-singer/umc.cmc.2bsarswmnr0ll0kdwrsij3nrh?action=play` | failed | Apple routed the series to Spectrum’s TV-provider sign-in. No account was selected and FOX One was not foregrounded. |
| FXNOW | `https://www.fxnetworks.com/shows/the-bear` | failed | Companion refused the URL. The TV stayed on Home. |
| FXNOW | `https://tv.apple.com/us/show/the-bear/umc.cmc.javg04xbn3eonbgfvnaqmodk?action=play` | unconfirmed | The Bear S5 E4 played with captions, but neither MCP nor the image identified the provider. This confirms title playback only. |
| Fox News | `https://www.foxnews.com/video/5614615980001` | failed | Companion refused the URL. The TV stayed on Home. |
| Global TV | `https://www.globaltv.com/shows/survivor/` | failed | Companion refused the URL. The TV stayed on Home. |
| Global TV | `https://tv.apple.com/ca/show/survivor/umc.cmc.6ozd0mt09a86bpa19l885jv4z?action=play` | failed | Apple handed Survivor to Spectrum’s TV-provider sign-in. Global TV did not open. |
| HBO Max | `https://www.hbomax.com/show/a8484031-f244-4661-9fb7-0932bd1ba872` | failed | Companion refused the URL. The TV stayed on Home. |
| HBO Max | `https://play.hbomax.com/show/a8484031-f244-4661-9fb7-0932bd1ba872` | app_home | HBO Max displayed its Who’s Watching picker with a profile focused. Succession was not yet visible. |
| HBO Max | `https://tv.apple.com/us/show/succession/umc.cmc.3trf3c8kzd8m4w72c9q8dwjqe?action=play` | failed | Apple handed Succession to Spectrum’s TV-provider sign-in. HBO Max was not observed on this route. |
| HBO Max | `https://play.hbomax.com/video/watch/e457df99-f817-4646-b64c-8ce7afaeb405` | playing | HBO Max played Celebration after a profile was selected. The episode link worked on relaunch. |
| Hulu | `https://www.hulu.com/series/only-murders-in-the-building-ef31c7e1-cd0f-4e07-848d-1cbfedb50ddf` | failed | Companion refused the Hulu website series URL. The TV stayed on Home. |
| Hulu | `hulu://series/ef31c7e1-cd0f-4e07-848d-1cbfedb50ddf` | title_page | Confirmed Open in Hulu. The series page offered S1 E1 True Crime in the Only Murders catalog. |
| Hulu | `https://tv.apple.com/us/show/only-murders-in-the-building/umc.cmc.7kd9yvyu5ecz1snkt4a35o12g?action=play` | title_page | The Apple TV handoff opened Hulu’s series page with S1 E1 True Crime ready to start. |
| Hulu | `hulu://watch/e2957562-1a0f-49ec-96d4-97aaf3cac2d5` | title_page | The Hulu episode URI stopped on the series page. Selecting Start Watching separately played S1 E1 True Crime. |
| Instagram | `https://www.instagram.com/natgeo/` | failed | Companion refused the creator profile URL. The TV stayed on Home. |
| MUBI | `https://mubi.com/en/us/films/the-substance` | failed | Companion refused the MUBI catalog URL. The TV stayed on Home. |
| MUBI | `https://tv.apple.com/us/movie/the-substance/umc.cmc.3fc032hv09dz1k89a1fa47396?action=play` | failed | The Apple handoff showed a black frame with stale paused metadata. Back revealed Hulu on its previous series page. |
| Music | `https://music.apple.com/us/album/rumours/594061854` | title_page | The album URL opened Rumours. Selecting Play separately played Second Hand News at full track length. |
| NBC News | `https://www.nbcnews.com/now` | failed | Companion refused the NBC News NOW URL. The TV stayed on Home. |
| NBC News | `https://www.nbcnews.com/tab/watch` | failed | Companion refused the association file’s Watch tab URL. The TV stayed on Home. |
| NBC Sports | `https://www.nbcsports.com/watch/premier-league` | failed | Companion refused the Premier League hub URL. The TV stayed on Home. |
| NBC | `https://www.nbc.com/saturday-night-live` | app_home | NBC opened its Shows A–Z directory. The Saturday Night Live title page was absent. |
| NBC | `https://tv.apple.com/us/show/saturday-night-live/umc.cmc.3p5hey1eeuk85gse9d27zaa5q?action=play` | app_home | The Apple handoff opened NBC’s tracking-permission prompt. Menu did not dismiss it. No privacy choice was made. |
| NBC | `https://tv.apple.com/us/show/saturday-night-live/umc.cmc.3p5hey1eeuk85gse9d27zaa5q?action=play` | playing | Apple handed Saturday Night Live to NBC and playback started. The earlier tracking prompt was absent. Free and account access could not be distinguished. |
| NBC | `nbctve://live/nbc` | app_home | Confirmed Open in NBC. Live TV requested TV-provider access. Don’t Allow was targeted but focus verification failed, and the TV returned Home. |
| NFL | `https://www.nfl.com/network` | failed | Companion refused the NFL Network page. The TV stayed on Home. |
| NFL | `https://www.nfl.com/videos/nfl-100-greatest-no-3-david-tyree-s-helmet-catch` | failed | Companion refused the NFL clip URL. The TV stayed on Home. |
| NFL | `nflmobile://watch/nfl` | failed | Companion refused the nflmobile live scheme. The TV stayed on Home. |
| Netflix | `https://www.netflix.com/title/80057281` | unconfirmed | Bridge reported tv_unreachable during launch. Home remained visible. Retry this candidate. |
| Netflix | `nflx://www.netflix.com/title/80057281` | app_home | Confirmed Open in Netflix and chose a profile. Netflix opened Home with a different featured series. |
| Paramount+ | `https://www.paramountplus.com/shows/star-trek-strange-new-worlds/` | failed | Companion refused the Paramount+ show URL. The TV stayed on Home. |
| Paramount+ | `https://tv.apple.com/us/show/star-trek-strange-new-worlds/umc.cmc.1o10sclnk0t945h2mtuolocip?action=play` | failed | S1 E1 played in Apple TV. Back showed TV’s Try Paramount+ Free offer. The standalone Paramount+ app was not observed. |
| Paramount+ | `https://www.paramountplus.com/shows/star-trek-strange-new-worlds/video/vD_dS02AvEtutf2mdwdiOU0WZIpkhMPA/star-trek-strange-new-worlds-strange-new-worlds/` | failed | Companion refused the Paramount+ episode URL. The TV stayed on Home. |
| Peacock | `https://www.peacocktv.com/stream-tv/the-office` | failed | Companion refused Peacock’s marketing URL. The TV stayed on Home. |
| Peacock | `https://tv.apple.com/us/show/the-office/umc.cmc.455js879szmdywutf3qjewagm?action=play` | title_page | Apple TV required Open In. Selected a Peacock profile and found The Office S9 E3 with Upgrade to Watch. No upgrade was selected. |
| Peacock | `https://www.peacocktv.com/watch/asset/tv/the-office/4902514835143843112` | app_home | Peacock opened its Home shelf featuring Roseanne instead of The Office. |
| Peacock | `https://www.peacocktv.com/watch/asset/tv/the-office/4902514835143843112/seasons/1/episodes/pilot-episode-1/58fabbf4-9291-3575-bcdc-bd25eee192c8` | app_home | The episode asset opened the Peacock Home carousel featuring Nobody. |
| Podcasts | `https://podcasts.apple.com/us/podcast/planet-money/id290783428` | app_home | The Planet Money URL opened the Apple Podcasts welcome screen. The run ended before Continue was selected. |

[Machine-readable catalog](../app/atv_bridge_app/data/deeplinks.validated.yaml) · [Validator](../spikes/deeplinks/validate.py)
