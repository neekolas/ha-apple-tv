# SPDX-License-Identifier: GPL-3.0-or-later
"""Turn swipes on a Siri Remote's touch surface into arrow keys, as tvOS does.

A first-generation Siri Remote has no arrow buttons. Every click logs the same
Select line. The finger position appears only in backboardd's debug log. This
module reads those lines and turns swipes into arrow steps, with momentum after
a brisk swipe or a flick. Taps do nothing. Clicks are handled by the press matcher.

It belongs to the press matcher in buttons.py: change only these two places when
a remote or a tvOS version logs something different.
Verified against a capture from a first-generation Siri Remote on tvOS 26.6.1
(tests/fixtures/touch_remote.jsonl). The tuning is preliminary. On the clickpad
remote (tvOS 27.2) a swipe moved one item, and its lines have not been captured.
Each contact that ends therefore gives a report (see Gesture.reports), which the
app logs. Use those reports to tune the values below.
"""

import math
import re
import statistics
from collections import deque
from dataclasses import dataclass, field

# --- Matcher ---------------------------------------------------------------
# Example: "touchpad Digitizer token: 0xB5890338; move; edge; gen: 256; subevents:
#           [path: 1; {0.0224, 0.6851}; move; edge]; move; edge"
# x and y run from 0 to 1. y grows downward. Lines arrive about every 15 ms.
CATEGORY = "TouchEvents"
EVENT = re.compile(r"touchpad Digitizer token: (\S+); (?:(cancel); )?.*?subevents: \[(.*)\]")
PATH = re.compile(r"path: (\d+); \{([-0-9.e]+), ([-0-9.e]+)\}(?:; (down|move|up|range \(in\)|range \(out\)))?")
# Other lines of the category on tvOS 26.6.1. They carry no finger position.
OTHER = re.compile(
    r"touches:\d+ ranges:\d+ |soft cancel |touchpad: Cancel |Cancel all touches |init: <|digitizer attached |Found a new touchpad "
)
# The device behind a touch surface. "digitizer attached" and "Found a new touchpad" lines name it.
DEVICE = re.compile(r"IOHIDService name:(\S+) id:(\S+).*?transport:(\S+)")
# A line of another log category that may be about touch. The watch logs the first one of each category.
HINT = re.compile(r"touch|digitizer", re.IGNORECASE)

# --- Tuning (pad units are fractions of the pad; times in seconds) -----------
# The surface. A first-generation remote reports 0 to 1 on both axes. A surface that reports
# another range gets its distances and speeds divided by the range that was seen.
PAD_SPAN = 1.0  # The coordinate range that counts as one pad until another range is known.
SPAN_MIN_SWIPES = 4  # Swipes to see before a range smaller than PAD_SPAN is believed.
SPAN_FULL = 0.9  # A seen range of at least this share of PAD_SPAN is a full pad. A finger rarely reaches the edge.
SPAN_MIN = 0.5  # A seen range counts as at least this. A wrong estimate can then at most double a distance.
# Steps.
STEP_DISTANCE = 0.4  # Finger travel on the dominant axis for one arrow.
AXIS_DOMINANCE = 1.5  # The dominant axis must move this many times farther than the other.
REVERSAL_DISTANCE = 0.08  # Moving back this far measures the next step from the turning point.
# Lift-off.
LIFT_WINDOW_S = 0.07  # Lift-off velocity uses the move samples of this last stretch, and at least the last two.
LIFT_REST_MAX_S = 0.05  # A finger that rested longer than this before lifting did not flick.
LIFT_REST_INTERVALS = 3  # On a surface that reports slowly, the rest limit is this many sample intervals.
PEAK_MIN_WINDOW_S = 0.03  # The peak speed of a report ignores a window shorter than this. Two lines can share a time.
# Momentum. Values from the first-generation capture: its four ordinary swipes lifted off at
# 5.2, 6.6, 9.4 and 9.8 pad units per second, and its two slow drags at 0.5 and 0.7.
FLICK_MIN_TRAVEL = 0.15  # A contact that covered less than this on the axis gives no flick. It guards a sloppy tap.
FLICK_V_MIN = 4.5  # Lift-off speed, in pad units per second, where momentum starts with one arrow.
FLICK_V_PER_STEP = 4.0  # Each further this much speed adds one momentum arrow.
MOMENTUM_MAX_EXTRA = 4  # Most momentum arrows after one flick.
MOMENTUM_FIRST_GAP_S = 0.04  # Wait before the first momentum arrow.
MOMENTUM_LAST_GAP_S = 0.12  # The gaps grow to this, so the motion looks like it slows down.
LOST_CONTACT_S = 0.5  # A tracked finger that logged nothing for this long gives way to a new touch.
CLICK_GUARD_S = 0.3  # A contact that starts this soon after a button press belongs to the click.
DEVICES_KEPT = 4  # Device names remembered for the reports.

AXES = (("left", "right"), ("up", "down"))


def momentum_count(speed):
    """How many extra arrows a lift-off speed earns: 1 from FLICK_V_MIN, then 1 more per FLICK_V_PER_STEP."""
    if speed < FLICK_V_MIN:
        return 0
    return min(MOMENTUM_MAX_EXTRA, 1 + math.floor((speed - FLICK_V_MIN) / FLICK_V_PER_STEP))


def momentum_delays(count):
    """Seconds after lift-off at which each momentum arrow goes out. The gaps grow."""
    delays, at = [], 0.0
    for index in range(count):
        share = index / (MOMENTUM_MAX_EXTRA - 1) if MOMENTUM_MAX_EXTRA > 1 else 0
        at += MOMENTUM_FIRST_GAP_S + (MOMENTUM_LAST_GAP_S - MOMENTUM_FIRST_GAP_S) * share
        delays.append(round(at, 4))
    return delays


@dataclass
class Gesture:
    """What one log line means for the page."""

    keys: list = field(default_factory=list)  # Arrows to send now.
    momentum: tuple | None = None  # (key, delays) to send after a flick.
    touch_down: bool = False  # A new touch. It cancels pending momentum arrows.
    reports: list = field(default_factory=list)  # One dict per contact that ended. See TouchRecognizer._report.
    unmatched: bool = False  # The line has a shape that this module does not know.


class Contact:
    def __init__(self, path, point, at, clicked):
        self.path = path
        self.anchor = list(point)  # Where the current step measurement starts.
        self.lead = list(point)  # The farthest point reached from the anchor, per axis.
        self.steps = 0
        self.clicked = clicked
        self.samples = deque(maxlen=16)  # (device time, x, y) of the last moves.
        self.seen_at = None  # Device time of the last line for this contact.
        self.moved_at = None  # Device time of the last change of position. A still finger logs nothing.
        # For the report.
        self.started = at
        self.start = point
        self.low, self.high = list(point), list(point)
        self.count = 0  # Lines with a position.
        self.peak = [0.0, 0.0]  # Highest speed per axis, in coordinate units per second.
        self.keys = []  # Every arrow that this contact gave, without momentum.


class TouchRecognizer:
    """Feed it log lines in device-time order. It returns a Gesture for each line.

    Rules, per contact (finger down to finger up):
    - Step: each STEP_DISTANCE of travel on the dominant axis gives one arrow at once,
      before the finger lifts. A slow drag moves one item per step.
    - Momentum: a lift-off at FLICK_V_MIN or faster gives extra arrows in the same direction.
      A brisk swipe gives one or two, a hard flick up to MOMENTUM_MAX_EXTRA.
      A flick too short for a step also gives its one arrow.
    - Click: a button press during the contact cancels its steps and momentum.
    - A tap gives nothing.
    All timing uses the device time of the log lines, never the host's arrival time.
    """

    def __init__(self):
        self.contacts = {}  # At most one entry: the first finger. More fingers are ignored.
        self.button_at = float("-inf")
        # What is known about the surface. It describes the remote, so reset() keeps it.
        self.token = None  # Digitizer token of the last touch line.
        self.devices = []  # Devices that announced themselves, newest last.
        self.extent = None  # [[lowest, highest] x, [lowest, highest] y] of the positions seen.
        self.swipes = 0  # Contacts that gave an arrow. They show that the finger used the surface.

    def reset(self):
        self.contacts.clear()

    def button(self, at):
        """A button went down. A contact under the finger belongs to that click."""
        self.button_at = at
        for contact in self.contacts.values():
            contact.clicked = True

    def span(self):
        """The coordinate range that counts as one pad. Distances and speeds are divided by it."""
        seen = max(high - low for low, high in self.extent) if self.extent else 0.0
        if seen > PAD_SPAN:
            return seen  # The surface does not report 0 to 1.
        if self.swipes < SPAN_MIN_SWIPES or seen >= SPAN_FULL * PAD_SPAN:
            return PAD_SPAN
        return max(SPAN_MIN, seen)

    def touch(self, message, at):
        gesture = Gesture()
        message = message or ""
        if device := DEVICE.search(message):
            name = " ".join(device.groups())
            self.devices = ([known for known in self.devices if known != name] + [name])[-DEVICES_KEPT:]
        found = EVENT.search(message)
        if not found:
            gesture.unmatched = not OTHER.search(message)
            return gesture
        token, cancel, body = found.groups()
        if token != self.token:
            # Another surface. What was learned about the old one does not apply.
            self.token, self.extent, self.swipes = token, None, 0
        if cancel:
            gesture.reports += [self._report(contact, at, "cancelled") for contact in self.contacts.values()]
            self.reset()
            return gesture
        paths = PATH.findall(body)
        gesture.unmatched = not paths
        for path, x, y, kind in paths:
            point = (float(x), float(y))
            if kind == "down":
                gesture.touch_down = True
                # Track one finger. A second finger would double every step.
                # A lift-off line can be lost, so a silent contact must not block the next touch.
                if any(path != other and at - contact.seen_at < LOST_CONTACT_S for other, contact in self.contacts.items()):
                    continue
                gesture.reports += [self._report(contact, at, "no_lift") for contact in self.contacts.values()]
                self.contacts = {path: Contact(path, point, at, at - self.button_at < CLICK_GUARD_S)}
            contact = self.contacts.get(path)
            if contact is None:
                continue
            if kind in ("up", "range (out)"):
                del self.contacts[path]
                self._lift(contact, gesture, at)
            else:
                self._sample(contact, point, at)
                if key := self._step(contact, point):
                    gesture.keys.append(key)
                    contact.keys.append(key)
        return gesture

    @staticmethod
    def _dominant(delta):
        axis = 0 if abs(delta[0]) >= abs(delta[1]) else 1
        return axis if abs(delta[axis]) >= AXIS_DOMINANCE * abs(delta[1 - axis]) else None

    @staticmethod
    def _velocity(contact):
        """Coordinate units per second on each axis over the last samples, and the time they cover."""
        last = contact.samples[-1]
        window = [sample for sample in contact.samples if last[0] - sample[0] <= LIFT_WINDOW_S]
        if len(window) < 2 and len(contact.samples) > 1:
            window = [contact.samples[-2], last]  # A surface that reports slowly.
        elapsed = last[0] - window[0][0]
        if elapsed <= 0:
            return None, 0.0
        return [(last[axis + 1] - window[0][axis + 1]) / elapsed for axis in (0, 1)], elapsed

    @staticmethod
    def _interval(contact):
        """The usual time between two lines of this contact."""
        times = [sample[0] for sample in contact.samples]
        return statistics.median(later - earlier for earlier, later in zip(times, times[1:])) if len(times) > 1 else 0.0

    def _sample(self, contact, point, at):
        contact.seen_at = at
        contact.count += 1
        if not contact.samples or contact.samples[-1][1:] != point:
            contact.moved_at = at
        contact.samples.append((at, *point))
        if self.extent is None:
            self.extent = [[value, value] for value in point]
        for axis in (0, 1):
            contact.low[axis] = min(contact.low[axis], point[axis])
            contact.high[axis] = max(contact.high[axis], point[axis])
            self.extent[axis] = [min(self.extent[axis][0], point[axis]), max(self.extent[axis][1], point[axis])]
        velocity, elapsed = self._velocity(contact)
        if velocity and elapsed >= PEAK_MIN_WINDOW_S:
            contact.peak = [max(peak, abs(speed)) for peak, speed in zip(contact.peak, velocity)]

    def _step(self, contact, point):
        span = self.span()
        for axis in (0, 1):
            reach = contact.lead[axis] - contact.anchor[axis]
            now = point[axis] - contact.anchor[axis]
            if now * reach >= 0 and abs(now) >= abs(reach):
                contact.lead[axis] = point[axis]
            elif abs(point[axis] - contact.lead[axis]) >= REVERSAL_DISTANCE * span:
                contact.anchor[axis], contact.lead[axis] = contact.lead[axis], point[axis]
        if contact.clicked:
            return None
        delta = [point[axis] - contact.anchor[axis] for axis in (0, 1)]
        axis = self._dominant(delta)
        if axis is None or abs(delta[axis]) < STEP_DISTANCE * span:
            return None
        contact.steps += 1
        contact.anchor, contact.lead = list(point), list(point)
        return AXES[axis][delta[axis] > 0]

    def _lift(self, contact, gesture, at):
        span = self.span()
        velocity, _ = self._velocity(contact)
        lift = [speed / span for speed in velocity] if velocity else None
        count = 0
        if contact.clicked:
            reason = "clicked"
        elif lift is None:
            reason = "too_short"
        # Velocity from old samples says nothing after the finger stopped and rested.
        elif at - contact.moved_at > max(LIFT_REST_MAX_S, LIFT_REST_INTERVALS * self._interval(contact)):
            reason = "rest_before_lift"
        elif (axis := self._dominant(lift)) is None:
            reason = "no_dominant_axis"
        elif abs(lift[axis]) < FLICK_V_MIN:
            reason = "below_v_min"
        elif (contact.high[axis] - contact.low[axis]) / span < FLICK_MIN_TRAVEL:
            reason = "too_short"
        else:
            reason = None
            key = AXES[axis][lift[axis] > 0]
            count = momentum_count(abs(lift[axis]))
            if not contact.steps:
                gesture.keys.append(key)
                contact.keys.append(key)
            gesture.momentum = (key, momentum_delays(count))
        gesture.reports.append(self._report(contact, at, reason, lift, count))
        if contact.keys:
            self.swipes += 1

    def _report(self, contact, at, reason, lift=None, count=0):
        """Describe a contact that ended, for the log. Positions are as the TV logged them.

        Speeds are in pad units per second: coordinate units divided by span.
        no_momentum is None when momentum fired. Otherwise it is one of clicked, too_short,
        rest_before_lift, no_dominant_axis, below_v_min, cancelled, no_lift.
        """
        span = self.span()
        end = contact.samples[-1][1:]
        return {
            "token": self.token,
            **({"devices": list(self.devices)} if self.devices else {}),
            "path": int(contact.path),
            "samples": contact.count,
            "interval": round(self._interval(contact), 4),
            "start": [round(value, 4) for value in contact.start],
            "end": [round(value, 4) for value in end],
            "min": [round(value, 4) for value in contact.low],
            "max": [round(value, 4) for value in contact.high],
            "travel": [round(end[axis] - contact.start[axis], 4) for axis in (0, 1)],
            "duration": round(at - contact.started, 4),
            "rest": round(at - contact.moved_at, 4),
            "span": round(span, 4),
            "peak_v": [round(speed / span, 2) for speed in contact.peak],
            "lift_v": lift and [round(speed, 2) for speed in lift],
            "steps": contact.steps,
            "keys": list(contact.keys),
            "momentum": count,
            "no_momentum": reason,
        }
