# SPDX-License-Identifier: GPL-3.0-or-later
"""Turn swipes on a Siri Remote's touch surface into arrow keys, as tvOS does.

A first-generation Siri Remote has no arrow buttons. Every click logs the same
Select line. The finger position appears only in backboardd's debug log. This
module reads those lines and turns swipes into arrow steps, with momentum after
a fast flick. Taps do nothing. Clicks are handled by the press matcher.

It belongs to the press matcher in buttons.py: change only these two places when
a remote or a tvOS version logs something different.
Verified against a capture from a first-generation Siri Remote on tvOS 26.6.1
(tests/fixtures/touch_remote.jsonl). The tuning is preliminary. The clickpad
remote is untested.
"""

import math
import re
from collections import deque
from dataclasses import dataclass, field

# --- Matcher ---------------------------------------------------------------
# Example: "touchpad Digitizer token: 0xB5890338; move; edge; gen: 256; subevents:
#           [path: 1; {0.0224, 0.6851}; move; edge]; move; edge"
# x and y run from 0 to 1. y grows downward. Lines arrive about every 15 ms.
CATEGORY = "TouchEvents"
EVENT = re.compile(r"touchpad Digitizer token: \S+; (?:(cancel); )?.*?subevents: \[(.*)\]")
PATH = re.compile(r"path: (\d+); \{([-0-9.e]+), ([-0-9.e]+)\}(?:; (down|move|up|range \(in\)|range \(out\)))?")

# --- Tuning (pad units are fractions of the pad; times in seconds) -----------
STEP_DISTANCE = 0.4  # Finger travel on the dominant axis for one arrow.
AXIS_DOMINANCE = 1.5  # The dominant axis must move this many times farther than the other.
REVERSAL_DISTANCE = 0.08  # Moving back this far measures the next step from the turning point.
LIFT_WINDOW_S = 0.07  # Lift-off velocity uses the move samples of this last stretch.
LIFT_REST_MAX_S = 0.05  # A finger that rested longer than this before lifting did not flick.
FLICK_V_MIN = 8.0  # Lift-off speed, in pad units per second, where momentum starts.
FLICK_V_PER_STEP = 4.0  # Each further this much speed adds one momentum arrow.
MOMENTUM_MAX_EXTRA = 4  # Most momentum arrows after one flick.
MOMENTUM_FIRST_GAP_S = 0.04  # Wait before the first momentum arrow.
MOMENTUM_LAST_GAP_S = 0.12  # The gaps grow to this, so the motion looks like it slows down.
LOST_CONTACT_S = 0.5  # A tracked finger that logged nothing for this long gives way to a new touch.
CLICK_GUARD_S = 0.3  # A contact that starts this soon after a button press belongs to the click.

AXES = (("left", "right"), ("up", "down"))


def momentum_count(speed):
    """How many extra arrows a lift-off speed earns."""
    return max(0, min(MOMENTUM_MAX_EXTRA, math.floor((speed - FLICK_V_MIN) / FLICK_V_PER_STEP)))


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
    lift_speed: float | None = None  # Signed lift-off speed on the dominant axis, for tuning.


class Contact:
    def __init__(self, point, clicked):
        self.anchor = list(point)  # Where the current step measurement starts.
        self.lead = list(point)  # The farthest point reached from the anchor, per axis.
        self.steps = 0
        self.clicked = clicked
        self.samples = deque(maxlen=16)  # (device time, x, y) of the last moves.
        self.seen_at = None  # Device time of the last line for this contact.
        self.moved_at = None  # Device time of the last change of position. A still finger logs nothing.


class TouchRecognizer:
    """Feed it log lines in device-time order. It returns a Gesture for each line.

    Rules, per contact (finger down to finger up):
    - Step: each STEP_DISTANCE of travel on the dominant axis gives one arrow at once,
      before the finger lifts. A slow drag moves one item per step.
    - Flick: a fast lift-off gives extra arrows in the same direction (momentum).
      A flick too short for a step still gives its one arrow.
    - Click: a button press during the contact cancels its steps and momentum.
    - A tap gives nothing.
    All timing uses the device time of the log lines, never the host's arrival time.
    """

    def __init__(self):
        self.contacts = {}  # At most one entry: the first finger. More fingers are ignored.
        self.button_at = float("-inf")

    def reset(self):
        self.contacts.clear()

    def button(self, at):
        """A button went down. A contact under the finger belongs to that click."""
        self.button_at = at
        for contact in self.contacts.values():
            contact.clicked = True

    def touch(self, message, at):
        gesture = Gesture()
        found = EVENT.search(message or "")
        if not found:
            return gesture
        if found[1]:
            self.reset()
            return gesture
        for path, x, y, kind in PATH.findall(found[2]):
            point = (float(x), float(y))
            if kind == "down":
                gesture.touch_down = True
                # Track one finger. A second finger would double every step.
                # A lift-off line can be lost, so a silent contact must not block the next touch.
                if any(path != other and at - contact.seen_at < LOST_CONTACT_S for other, contact in self.contacts.items()):
                    continue
                self.contacts = {path: Contact(point, at - self.button_at < CLICK_GUARD_S)}
            contact = self.contacts.get(path)
            if contact is None:
                continue
            if kind in ("up", "range (out)"):
                del self.contacts[path]
                self._lift(contact, gesture, at)
            else:
                contact.seen_at = at
                if not contact.samples or contact.samples[-1][1:] != point:
                    contact.moved_at = at
                contact.samples.append((at, *point))
                if key := self._step(contact, point):
                    gesture.keys.append(key)
        return gesture

    @staticmethod
    def _dominant(delta):
        axis = 0 if abs(delta[0]) >= abs(delta[1]) else 1
        return axis if abs(delta[axis]) >= AXIS_DOMINANCE * abs(delta[1 - axis]) else None

    def _step(self, contact, point):
        for axis in (0, 1):
            reach = contact.lead[axis] - contact.anchor[axis]
            now = point[axis] - contact.anchor[axis]
            if now * reach >= 0 and abs(now) >= abs(reach):
                contact.lead[axis] = point[axis]
            elif abs(point[axis] - contact.lead[axis]) >= REVERSAL_DISTANCE:
                contact.anchor[axis], contact.lead[axis] = contact.lead[axis], point[axis]
        if contact.clicked:
            return None
        delta = [point[axis] - contact.anchor[axis] for axis in (0, 1)]
        axis = self._dominant(delta)
        if axis is None or abs(delta[axis]) < STEP_DISTANCE:
            return None
        contact.steps += 1
        contact.anchor, contact.lead = list(point), list(point)
        return AXES[axis][delta[axis] > 0]

    def _lift(self, contact, gesture, at):
        if contact.clicked or len(contact.samples) < 2:
            return
        # Velocity from old samples says nothing after the finger stopped and rested.
        if at - contact.moved_at > LIFT_REST_MAX_S:
            return
        last = contact.samples[-1]
        window = [sample for sample in contact.samples if last[0] - sample[0] <= LIFT_WINDOW_S]
        elapsed = last[0] - window[0][0]
        if elapsed <= 0:
            return
        velocity = [(last[axis + 1] - window[0][axis + 1]) / elapsed for axis in (0, 1)]
        axis = self._dominant(velocity)
        if axis is None:
            return
        gesture.lift_speed = velocity[axis]
        speed = abs(velocity[axis])
        key = AXES[axis][velocity[axis] > 0]
        if speed >= FLICK_V_MIN and not contact.steps:
            gesture.keys.append(key)
        if count := momentum_count(speed):
            gesture.momentum = (key, momentum_delays(count))
