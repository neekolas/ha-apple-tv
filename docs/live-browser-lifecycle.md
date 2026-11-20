# Chromium lifecycle on Home Assistant Green

On November 19, 2026, the Green mirrored two different inline HTML pages per cycle to an Apple TV 4K (2nd generation) running tvOS 27.2 (24K122). This was a single-receiver hardware test. The agent called the deployed bridge through Home Assistant MCP. Each page change had a distinct page ID and a verified mirror frame. Page B reused the sender that displayed page A.

The first ten cycles stopped normally. In the last two, a test build sent SIGKILL to its own tvpipe process while page A was showing. The test waited for a new sender PID and a positive transmitted frame count before calling another tool. The app registers the crash test route only when `ATV_BRIDGE_LIFECYCLE_TEST=1`. It requires the internal bearer token.

Each active sample followed page B. Each idle sample followed the hide response. `tv_show {}` dismissed the page in this single-TV test. With multiple paired TVs, dismiss the intended page with `tv_show {"tv":"<room>"}`. Browser count and summed RSS came from `data.browser` in a single-TV `tv_status` result. In a fleet, call `tv_status {"tv":"<room>"}` or read `data.tvs[].status.browser` in an unscoped summary. These fields count browser processes in the entire app container. Each receiver reports the same scope. Container memory came from Supervisor's `/addons/local_atv_bridge/stats` via `ha apps stats local_atv_bridge --raw-json`. All memory values are MiB. Summed process RSS counts shared pages once per process, so it can exceed total container memory.

| Cycle | Sender SIGKILL and recovery (PIDs) | Hide call | Active processes | Active browser RSS | Active container | Idle processes | Idle browser RSS | Idle container |
| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Before first show | — | — | — | — | — | 0 | 0.00 | 141.93 |
| 1 | — | `tv_hide {}` | 11 | 1163.40 | 659.71 | 0 | 0.00 | 304.11 |
| 2 | — | `tv_show {}` | 11 | 1210.27 | 652.29 | 0 | 0.00 | 316.09 |
| 3 | — | `tv_hide {}` | 11 | 1247.04 | 677.40 | 0 | 0.00 | 316.89 |
| 4 | — | `tv_show {}` | 11 | 1293.34 | 724.54 | 0 | 0.00 | 316.80 |
| 5 | — | `tv_hide {}` | 11 | 1195.71 | 645.77 | 0 | 0.00 | 316.88 |
| 6 | — | `tv_show {}` | 11 | 1196.91 | 647.29 | 0 | 0.00 | 317.09 |
| 7 | — | `tv_hide {}` | 11 | 1234.48 | 675.46 | 0 | 0.00 | 317.46 |
| 8 | — | `tv_show {}` | 11 | 1222.65 | 672.92 | 0 | 0.00 | 317.52 |
| 9 | — | `tv_hide {}` | 11 | 1233.80 | 680.46 | 0 | 0.00 | 317.38 |
| 10 | — | `tv_show {}` | 11 | 1222.56 | 664.33 | 0 | 0.00 | 317.73 |
| 11 | 2179 → 2373 | `tv_hide {}` | 11 | 1227.11 | 680.03 | 0 | 0.00 | 317.92 |
| 12 | 2591 → 2789 | `tv_show {}` | 11 | 1208.47 | 654.34 | 0 | 0.00 | 318.68 |

Both killed senders recovered without another show request. The app logged detached Chromium processes and crashpad handlers by PID as it reaped them. Every hide returned to zero browser processes and zero RSS. After the first cycle warmed the container, Supervisor reported 316.09–318.68 MiB idle in cycles 2–12.

## Cold and warm memory

A separate show and hide on the production build measured the Green's cgroup directly. The paired observer was running before the show. The production deployment had the crash-test flag disabled.

| Measurement (MiB) | Before first show | After hide | Difference |
| --- | ---: | ---: | ---: |
| Supervisor container memory | 148.18 | 386.53 | +238.35 |
| Anonymous pages (`anon`) | 133.39 | 133.56 | +0.17 |
| File-backed pages (`file`) | 18.53 | 258.11 | +239.58 |
| Shared memory (`shmem`) | 0.00 | 0.00 | 0.00 |
| Browser RSS | 0.00 | 0.00 | 0.00 |

Anonymous memory returned within 0.17 MiB of its initial value. Linux retained file-backed pages after the browser exited. This accounts for the higher Supervisor memory reading after hide. The kernel can reclaim that cache under memory pressure.

The final accessibility read in that run confirmed Home (`com.apple.HeadBoard`) with YouTube focused. The final status reported healthy control and screen pairings, zero browser processes and zero browser RSS. [Browser accounting](../app/atv_bridge_app/present/browsers.py) · [Lifecycle tests](../app/tests/test_browser_lifecycle.py)
