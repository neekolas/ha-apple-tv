# Making a mirrored page feel like a tvOS app

**Question.** A page mirrored with `tv_show` gets Siri Remote input as arrow keys and Enter. What would make it feel like a native tvOS app, at 10 fps and with 150–250 ms of input delay, with little added code?

**Status: source review only.** Nothing here was measured on the Home Assistant Green or seen on a TV. A claim marked *(judgment)* is my own conclusion. A claim marked *(speculation)* has no source behind it. Web sources were read on 2026-11-20. Code references use the working tree on top of `d635b83`. `touch.py` and `buttons.py` had uncommitted edits at that time, so the constants quoted here may change.

## Verdict

Extend the existing focus helper. Do not adopt a library. Add a third mode, `remote: "tvos"`, that keeps the helper's navigation and adds four things: a lift look (scale, shadow, white ring), a search rule closer to UIKit, memory of the last-focused item per row, and one retargeted scroll for fast key runs. The total is about 5–6 KB of unminified JS and CSS.

Skip the touch-driven parallax tilt and skip sound cues. Both depend on timing that this pipeline cannot give.

I found no published project that does this exact thing (a remote-driven page mirrored at a low frame rate). The parts exist separately. Section 2 lists them.

## 1. How tvOS feels

### What Apple documents

- **Focus is shown by depth, not by a ring.** tvOS generally uses the parallax effect for the focused item. The item comes to the foreground, sways gently and gets a shine. After a period without input, the other content dims and the focused item grows. [HIG-Focus] [HIG-Images]
- **Parallax is meant to be nearly invisible.** The HIG says strong 3D effects look wrong. [HIG-Images]
- **Focused items grow**, so the HIG asks for padding between focusable items. [HIG-Layout]
- **A focusable item has up to five states**, and choosing an item gives instant visual feedback. [HIG-Focus]
- **No pointer.** People expect to move focus between a fixed set of items. [HIG-Focus]
- **Swipes have momentum.** Movement starts fast and slows down, based on the strength of the swipe. A swipe on the edge of the remote goes through items very quickly. A tap without a press can be an accident. [HIG-Remotes]
- **Layout.** Keep main content 60 pt from the top and bottom and 80 pt from the sides. Use a grid. [HIG-Layout]
- **Type.** Body text is 29 pt and the smallest style is 23 pt. Titles are 76, 57 and 48 pt. [HIG-Type] On a 1920×1080 mirror, 1 pt is 1 CSS pixel *(judgment)*.
- **Focus search.** The engine finds the focusable items in the direction of the swipe and focuses the first one. If there is none, focus stays. `UIFocusGuide` adds an invisible region that sends focus to another item. [UIKit-Add] [UIKit-About] [UIKit-Guide]
- **Animation timing is not fixed.** Apple publishes no duration, easing or scale factor. The system speeds up the animation when focus moves quickly, focusing usually runs faster than unfocusing (this leaves a trail), and code must not assume a fixed duration. [UIKit-Coord]
- **Row memory.** `remembersLastFocusedIndexPath` returns focus to the item of a collection view that had it last. It is off by default. [UIKit-Remember]
- **Movement hint.** `UIFocusMovementHint` (tvOS 12 and later) gives `movementDirection`, described as how close focus is to moving to another item, plus a rotation and a translation that match the system's hinting. [UIKit-Hint] I read this as the API behind the lean of the focused item while the finger moves *(judgment)*.

### What the sessions say

Apple's site now redirects all three session pages to an index. The text below comes from machine-made transcripts on a third-party mirror, so the wording may be wrong.

- **2015 Tech Talk.** The engine searches a rectangle that starts at the focused view and extends in the swipe direction. The closest focusable view in it wins. A far or fast swipe can pass several views. The engine changes the scroll offset so that the focused item is always fully visible. A slight finger movement tilts the focused item. [TT15]
- **WWDC16 215.** Motion effects move a control slightly while the thumb rests on the touch surface, by less than the amount that moves focus. [W16]
- **WWDC17 224.** Each focus move plays a sound. Faster moves are quieter. The sound is panned by the item's horizontal position. When focus moves to an item that is off screen, the focusing animation waits until the item is fully visible. [W17]

### Not found

- No Apple source gives scoring weights for focus search, the velocity-to-item mapping for momentum, or the tilt angle and shift distance of parallax.
- No primary source was found for click-and-hold behavior. The HIG says only that people press before swiping to start scrubbing. [HIG-Remotes]

## 2. Prior art

Sizes are measured from the published files. "min" means the minified build. Dates come from the GitHub API and the npm registry.

| Project | What it gives | Size | License, status | Fit |
|---|---|---|---|---|
| CSS Spatial Navigation Level 1 [CSSNAV] | W3C Working Draft (2019-11-26). Defines `navigate()`, `spatialNavigationSearch()`, containers, two events, and a distance function: euclidean distance + displacement − alignment − √overlap, with weights tuned by test | Spec | I found no shipping browser API | Reference for the distance function and for the rule that each scroll container is a container |
| WICG polyfill [WICG] | The spec in JS | 73.5 KB source, 14.1 KB gzip | MIT on npm. Archived. Last release 2019 | No. Large and unmaintained |
| Chromium `--enable-spatial-navigation` [CR-SW] [CR-SN] | Blink's own arrow-key navigation (`SpatialNavigationController`) | 0 KB | In current Chromium source | Not tested in our headless Chromium or in the sandboxed content frame. No control of the look, no row memory. A later experiment, not a base |
| BBC `lrud-spatial` [LRUDS] | One function, `getNextFocus(el, key, scope)`. Scans the DOM. Containers are `nav`, `section`, `.lrud-container`. `data-focus` remembers the last item. `data-block-exit` blocks a direction. Distance is exit edge to entry edge | 10.3 KB min, 3.1 KB gzip | Apache-2.0. Last push 2026-07 | **Best match in design.** The build is CommonJS, so it needs a wrapper. Our helper already does about half of it in 3.3 KB |
| Norigin Spatial Navigation [NORIGIN] | Focus tree for React TV apps: last-focused child, focus boundaries, preferred child | Core 52 KB min, 16.7 KB gzip | MIT. Active | No. Each element must be registered with a key and callbacks. It does not scan the DOM |
| `js-spatial-navigation` [JSN] | Sections, selectors, last-focused memory, `sn:` events | 33.5 KB source | MPL-2.0. Last npm release 2017 | No. Old |
| react-native-tvos [RNTV] | Native tvOS focus from React Native. `TVFocusGuideView` has `autoFocus` (returns to the last child), `destinations` and focus trapping. `tvParallaxProperties` defaults: tilt 0.05, shift 2.0, press duration 0.3 | Native | MIT. Active | Not web. Useful as a list of features that people needed |
| TVML and TVJS [TVML] | Apple's markup for client-server apps. Each page is one fixed full-screen template with a default theme | — | Deprecated in tvOS 18 | No. Apple's own "markup pages with native focus" route is closed. The look of the templates was not assessed |
| atvImg [ATV] | The best-known web copy of the layered parallax icon. Pointer position tilts a stack of PNG layers and moves a shine | 7.4 KB JS, 1.2 KB CSS | MIT or GPL (file header). 2015 code | No. Needs layered art and a pointer |
| vanilla-tilt.js [TILT] | Tilts any element toward the pointer, with glare | 8.9 KB min, 2.5 KB gzip | MIT. Last release 2023 | No. Pointer-driven and continuous (see section 3) |
| webOS Magic Remote [WEBOS] | Pointer mode and 5-way mode. Every app must work in 5-way mode | Guidance | — | Confirms: do not build a pointer from the touch stream |
| Samsung Tizen [TIZEN] | Always show focus on exactly one item. Use a grid. Keep movement predictable | Guidance | — | Matches the helper's rules |

Not a fit, for one reason each: BBC LRUD [LRUD] (archived, the app builds the tree by hand), Enact Spotlight [ENACT] (needs React), LightningJS [LNG] (a WebGL renderer with no DOM, 339 KB min).

No primary source was found for Netflix's Gibbon focus or animation rules. The webOS, Tizen and react-native-tvos rows come from search-result summaries of the linked pages, not from a full read.

## 3. The 10 fps mirror

This whole section is *(judgment)*, built on arithmetic. The Green's rendering path is not measured. `render.py` starts Chromium with `--headless` and no GPU flags, so software rendering is likely *(speculation)*.

- **One frame is 100 ms.** A 200 ms transition shows at most 2 in-between frames. A 300 ms scroll over 600 px shows 3 jumps of about 200 px. Short animations become steps, and long movements become judder.
- **Changes that hold up:** one element changes state in place. Scale, shadow, opacity and color read correctly even when the mirror shows only the start and the end.
- **Changes that judder:** anything that moves far or moves all the time. Long smooth scrolls, a ring that slides between items, carousels, tilt that follows the finger, shimmer and spinners. A full-screen move also changes every pixel, and at about 4 Mbit/s the encoder will blur it.
- **Does a small lift still read as native?** Yes, as a state. People recognize the lift, the shadow and the brighter item. They will not get the sway. A scale below about 1.05 on a small button is too small to see from a sofa, so the ring stays.
- **Durations.** Use an ease-out curve so that most of the change lands in the first mirrored frame. Keep focus-in at 150–200 ms and focus-out a little longer, as UIKit does. A state that must be seen has to last 200 ms or more: a 100 ms state can fall between two mirrored frames.
- **Cost.** Animate only the scale of the one focused element. A `box-shadow` transition repaints on every frame, so set the shadow at once. Do not set `will-change` on every focusable item: each one becomes a layer and uses memory. The helper already reads all rectangles before it writes, which avoids layout thrash. A static screen costs the encoder almost nothing, so a look that settles and stops is the cheap choice.
- **Input delay.** The page cannot remove 150–250 ms. It can avoid adding to it: move focus in the same task as the key event, and never wait for an animation before taking the next key. The HIG has the same rule: let people cancel motion. [HIG-Motion] The helper already does this.

## 4. Recommendation

All numbers below are my choices *(judgment)*. Tune them on the TV.

### Shape

Add `remote: "tvos"`. Keep `focus` as it is, because it is the safe choice for a page we did not write. A scale on an unknown page can be clipped by `overflow: hidden`, can cover a neighbor, and creates a stacking context. `tvos` is for pages that an agent writes with the layout rules in mind.

### Focus look

- Scale 1.06 with the CSS `scale` property, not `transform`. An `!important` transform on `:focus` would replace a transform that the page set on that element. Use 1.03 for items wider than half the screen. Neither property applies to an inline link, which gets the ring only.
- Shadow `0 16px 40px rgba(0,0,0,.45)`, applied without a transition.
- Ring: keep one, but make it white, 3 px, with 3 px offset. tvOS has no ring on posters, but a ring is the only cue that survives on a text link and at low bitrate.
- Timing: scale in over 180 ms, out over 260 ms, both `cubic-bezier(.2,.8,.2,1)`.
- Pressed state: on Enter, scale to 1.0 and hold for 200 ms.
- Fast runs: when a key arrives within 150 ms of the last one, set a class on the root that suppresses scale and shadow. Items passed in a flick show only the ring. Remove the class 150 ms after the last key, so the final item lifts. This copies "faster movement, quicker animation".
- Skip the idle dimming of other content. It is a full-screen change for a small gain.

### Navigation

The current helper scores by center distance plus four times the gap on the other axis. Change three things:

1. **Beam first.** Project the focused box in the pressed direction (the 2015 Tech Talk rule). If any candidate overlaps that beam, pick the nearest one by edge-to-edge distance (the lrud-spatial measure), and ignore items outside the beam. Use the current scoring only when the beam is empty. This is an approximation: Apple documents no scoring.
2. **Groups with memory.** A group is the nearest ancestor that is a scroll container or has `data-tv-group`. When focus enters a group from outside, go to the group's last-focused item if it is still usable. This is `remembersLastFocusedIndexPath` and lrud-spatial's `data-focus`. Keep the memory in a `WeakMap`.
3. **Keep the scroll fallback.** tvOS keeps focus in place when no item lies in the direction. The helper lets the page scroll instead. A mirrored page with long text needs that.

`getBoundingClientRect()` returns the scaled box, sometimes in the middle of a transition. The helper must measure the focused item without its scale (divide by the current scale, or use `offsetWidth` and `offsetHeight` around the center). Otherwise the beam is too wide and can overlap a neighbor.

Do not add focus guides or a registration API. A page that needs custom routing can handle `keydown` first, since the helper skips a key that the page already handled.

### Scrolling and momentum

- Give focused items `scroll-margin` of 60 px top and bottom and 80 px at the sides (the tvOS safe area), so the next item shows at the edge.
- Scroll with `scrollIntoView({behavior: "smooth"})`, so each new key replaces the scroll target. Check on the TV that Chromium retargets a running smooth scroll. If it does not, use one `requestAnimationFrame` tween of about 250 ms per scroller (about 15 lines).
- The observer sends momentum arrows 40–120 ms apart (`touch.py`). When they reach the page depends on the CDP round trips in `send()`, which is not measured. Several can land inside one mirrored frame. Move focus on each arrow at once, with the fast-run class on, and let the one scroll be retargeted. The viewer sees one glide and the ring landing, not four separate jumps.
- This needs no protocol change. Sending the arrow count at lift-off would let the page jump straight to the end, but a new touch could then no longer stop the glide.

### Touch-driven parallax: skip

- The tilt on tvOS follows the finger closely. Here it would trail by 150–250 ms and update 10 times a second. That reads as a fault, not as depth.
- It keeps the picture changing for the whole touch, so Chromium and the encoder work on every frame.
- The finger position is verified only on the first-generation remote.

If the stream is exposed at all, expose it for pages that have a real use (scrubbing, panning a map). Send a `tvremote-touch` CustomEvent on `window` with `detail: {phase: "down" | "move" | "up", x, y, dx, dy, t}`. `x` and `y` are 0–1 with `y` growing downward, as the TV logs them. Limit it to 10 events per second: more cannot be seen.

### Sound cues: skip for now

The page's audio is mirrored unless `audio` is `mute`, so a short Web Audio tick is possible in under 1 KB with no file. The audio delay through the mirror is not measured. A tick that lands late feels worse than silence. Measure first.

### Size and delivery

- Estimated total: 5–6 KB unminified, about 2 KB gzip. The current helper is 3.3 KB. The CSS adds about 0.6 KB, groups about 0.8 KB, the beam pass about 0.3 KB, fast-run and pressed state about 0.5 KB, scrolling up to 0.5 KB.
- `RemoteInput.send()` evaluates the whole helper before every key. The guard on its first line makes that cheap in the page, but the full source still crosses CDP on each press. With a larger helper, let `PRESS_LOG` (which already runs before each key) report whether the helper is present, and send the helper only when it is missing.

### What to tell agents

A layout section in the skill does more for "native feel" than any script: 29 px body text or larger, 60 px and 80 px margins, items in rows and grids, 8% spare room around focusable items, dark background, no hover-only controls, and no animation that runs without input.

## Sources

Apple, current pages:

- [HIG-Focus] Human Interface Guidelines, Focus and selection. https://developer.apple.com/design/human-interface-guidelines/focus-and-selection
- [HIG-Remotes] HIG, Remotes. https://developer.apple.com/design/human-interface-guidelines/remotes
- [HIG-Images] HIG, Images (tvOS: parallax effect, layered images). https://developer.apple.com/design/human-interface-guidelines/images
- [HIG-Layout] HIG, Layout (tvOS). https://developer.apple.com/design/human-interface-guidelines/layout
- [HIG-Type] HIG, Typography (tvOS built-in text styles). https://developer.apple.com/design/human-interface-guidelines/typography
- [HIG-Motion] HIG, Motion. https://developer.apple.com/design/human-interface-guidelines/motion
- [UIKit-About] About focus interactions for Apple TV. https://developer.apple.com/documentation/uikit/about-focus-interactions-for-apple-tv
- [UIKit-Add] Adding user-focusable elements to a tvOS app. https://developer.apple.com/documentation/uikit/adding-user-focusable-elements-to-a-tvos-app
- [UIKit-Guide] UIFocusGuide. https://developer.apple.com/documentation/uikit/uifocusguide
- [UIKit-Coord] UIFocusAnimationCoordinator. https://developer.apple.com/documentation/uikit/uifocusanimationcoordinator
- [UIKit-Remember] remembersLastFocusedIndexPath. https://developer.apple.com/documentation/uikit/uicollectionview/rememberslastfocusedindexpath
- [UIKit-Hint] UIFocusMovementHint. https://developer.apple.com/documentation/uikit/uifocusmovementhint
- [TVML] TVML. https://developer.apple.com/documentation/tvml

Apple sessions, read from a third-party transcript mirror:

- [TT15] Apple TV Tech Talks 2015, "Focus-Driven Interfaces with UIKit". https://nonstrict.eu/wwdcindex/tech-talks/2015-apple-tv-3/
- [W16] WWDC 2016 session 215, "Focus Interaction on tvOS". https://nonstrict.eu/wwdcindex/wwdc2016/215/
- [W17] WWDC 2017 session 224, "Focus Interaction in tvOS 11". https://nonstrict.eu/wwdcindex/wwdc2017/224/

Web and TV:

- [CSSNAV] CSS Spatial Navigation Level 1, W3C Working Draft, 26 November 2019. https://www.w3.org/TR/css-nav-1/
- [WICG] https://github.com/WICG/spatial-navigation
- [CR-SW] Chromium `content/public/common/content_switches.cc`, `kEnableSpatialNavigation`. https://github.com/chromium/chromium/blob/main/content/public/common/content_switches.cc
- [CR-SN] Chromium `third_party/blink/renderer/core/page/spatial_navigation_controller.cc`. https://github.com/chromium/chromium/blob/main/third_party/blink/renderer/core/page/spatial_navigation_controller.cc
- [LRUDS] https://github.com/bbc/lrud-spatial
- [LRUD] https://github.com/bbc/lrud
- [NORIGIN] https://github.com/NoriginMedia/Norigin-Spatial-Navigation
- [JSN] https://github.com/luke-chang/js-spatial-navigation
- [ENACT] https://github.com/enactjs/enact
- [LNG] https://github.com/rdkcentral/Lightning
- [RNTV] https://github.com/react-native-tvos/react-native-tvos
- [ATV] https://github.com/drewwilson/atvImg
- [TILT] https://github.com/micku7zu/vanilla-tilt.js
- [WEBOS] LG webOS TV, Magic Remote. https://webostv.developer.lge.com/develop/guides/magic-remote
- [TIZEN] Samsung Smart TV design, input methods and UX checklist. https://developer.samsung.com/smarttv/design/input-methods.html
